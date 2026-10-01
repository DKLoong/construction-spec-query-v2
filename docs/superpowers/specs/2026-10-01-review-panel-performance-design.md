# 审核界面卡顿治理 — 设计文档

> 日期：2026-10-01
> 症状：`/review` 的「条文待审」「词面校核」两页在待审数量大时操作明显卡顿
> 状态：已定位根因，待实施

---

## 一、症状与复现

用户报告：「条文待审」和「词面校核」页中待审数量一多，操作起来就有明显的卡顿。

生产库实测规模（`data/spec_query.db`）：

| 指标 | 值 |
| --- | --- |
| `rule_pending` 总行数 | 4 996 |
| pending 行 | 4 138 |
| rejected 行 | 771 |
| Tab2 词面组数 | 1 302 |
| Tab1 条文组数 | 1 501 |
| `classification_queue` 行数 | 4 625（其中 review 态 1 569） |
| `clauses` 行数 | 3 044 |

---

## 二、实测证据

### 2.1 服务端耗时（实时库，非估算）

| 端点 | 服务端总耗时 | 返回 HTML 体积 |
| --- | --- | --- |
| `GET /review/clause-pending`（Tab1） | **1 452 ms** | **10.9 MB** |
| `GET /review/word-pending`（Tab2） | 41 ms | 1.85 MB |
| `GET /review/pending-count`（宫格红点） | **769 ms** | ~40 B |

函数级拆分：

| 函数 | 耗时 |
| --- | --- |
| `pending_clause_groups()`（Tab1 数据） | 861 ms |
| `rejected_clause_groups()`（**返回 0 行**） | 289 ms |
| `pending_groups()`（Tab2 数据） | 21 ms |
| `pending_counts()`（红点） | 781 ms |
| `_fetch_review_items()` | 136 ms |

### 2.2 数据库层：`classification_queue` 表零索引

`EXPLAIN QUERY PLAN` 实测输出：

```
SEARCH rp USING INDEX idx_rule_pending_status (status=?)
CORRELATED SCALAR SUBQUERY 1
SCAN q                          ← 4 138 个 pending 行，每行全表扫 4 625 行的 classification_queue
SEARCH c USING INTEGER PRIMARY KEY (rowid=?)
SEARCH s USING INTEGER PRIMARY KEY (rowid=?)
USE TEMP B-TREE FOR GROUP BY
```

`PRAGMA index_list(classification_queue)` 返回空——**该表没有任何索引**。同理，`rule_pending` 的四个索引全部以 `(dimension, pattern, ...)` 或 `(status)` 开头，**没有以 `clause_id` 打头的索引**，导致所有 `NOT EXISTS (… WHERE p2.clause_id=…)` 子查询也退化为全表扫描。

在副本库上验证加两条索引的效果：

```sql
CREATE INDEX idx_cq_clause_dim_status ON classification_queue(clause_id, dimension, status);
CREATE INDEX idx_rp_clause_dim_status ON rule_pending(clause_id, dimension, status);
```

| 查询 | 加索引前 | 加索引后 | 倍数 |
| --- | --- | --- | --- |
| Tab1 主查询（取行） | 375 ms | **19 ms** | 20× |
| 主表段 COUNT | 353 ms | **4 ms** | 88× |
| 全驳段 COUNT | 280 ms | **0.3 ms** | 935× |
| 兜底段 COUNT | 129 ms | **0.6 ms** | 215× |
| `pending_counts` 三段合计 | 769 ms | 约 **5 ms** | 154× |

建索引本身耗时：18 ms。两条索引均可由 `init_db()` 的 `executeschema` 幂等创建。

### 2.3 应用层：N+1 查询（**实测收益有限，见 4.2**）

- `pending_clause_groups()`：先取 1 501 个条文，再逐条单独查候选词 → 1 501 次往返
- `pending_groups()`：先取 1 302 个词面，再逐个单独查标签 → 1 302 次往返

补索引后实测：1 537 次逐条查询合计 **9 ms**，等价的单条 JOIN 查询 5 ms。**差值约 4 ms。**

### 2.4 前端：全量渲染，隐藏面板照常付费

用真实数据库生成 HTML 后交由 Chrome 实测：

| | DOM 节点 | DOMContentLoaded | 内联 `style=` | `<button>` |
| --- | --- | --- | --- | --- |
| Tab1 面板 | **55 527** | 953 ms | 46 423 处（占体积 35%） | 11 686 个 |
| Tab2 面板 | 22 613 | 511 ms | — | — |
| 两面板合计 | **81 780** | — | — | — |

体积构成（Tab1 主表段 9 924 KB）：

| 片段 | 体积 | 占比 |
| --- | --- | --- |
| 内联 `style` 属性 | 3 464 KB | 35% |
| `<button>` 开标签 | 2 266 KB | 23% |
| 纯文本节点 | 2 285 KB | 23% |
| `title` 属性（完整 `content` 副本） | 1 221 KB | 12% |

放大因素：

1. **`x-show` 只是 `display:none`，DOM 全在**。`review_tabs.html` 三个面板都用 `hx-trigger="load"` 在页面加载时立即请求，未激活 Tab 的 22 613 个节点一个不省。
2. **「编辑态」被全量预渲染**。每个卡片同时生成查看态与编辑态（chips、输入框、保存/取消按钮），用户点「✏️ 编辑」前这些 DOM 完全无用。

### 2.5 交叉刷新：Tab2 卡顿的直接原因

浏览器实测（写请求已拦截，未改动数据库）——在 **Tab2 点一次「批准」**后实际发出的请求：

```
POST /review/word-pending/decide     +695 ms
GET  /review/clause-pending          +723 ms → 3 980 ms 返回（3.3 秒）
GET  /review/word-pending            +723 ms
GET  /review/blacklist               +723 ms
GET  /review/pending-count           +723 ms   ← 同一次操作触发 3 次
GET  /review/pending-count           +723 ms
GET  /review/pending-count           +724 ms
```

此时 Tab1 面板 `display: none`（不可见），但 **55 521 个 DOM 节点被完整销毁重建**。

代码成因两处：

- `app/templates/partials/review_tabs.html:21` —— 条文待审面板的触发器为
  `hx-trigger="load, reviewClausePending from:body, reviewWordPending from:body, reviewBlacklist from:body"`，**监听全部三个事件**。
- `app/templates/partials/tree_panel.html:229` —— 宫格红点把三个事件绑到同一个 `refresh`，**一次操作跑 3 遍 769 ms 的 SQL**。

> 语义澄清：Tab1 监听 Tab2 事件本身**是正确的**——Tab2 批准后会 `backfill_and_close` 回填条文、该条文从 Tab1 消失。问题在于刷新的**代价太高**，而不是不该刷新。

---

## 三、根因结论

| # | 层 | 根因 | 量级 |
| --- | --- | --- | --- |
| R1 | 数据库 | `classification_queue` 零索引 + `rule_pending` 无 `clause_id` 打头索引 → 相关子查询全表扫描 | 后端 1.2 s 中的 ~1.0 s |
| R2 | 应用 | 无分页，单次物化全部 1 501 条 + 4 081 个候选词 | HTML 10.9 MB |
| R3 | 前端 | 全量渲染 + 隐藏面板不省 + 交叉刷新无可见性门控 | 解析 953 ms，操作后重建 55 K 节点 |

三者**都与待审数量线性/超线性相关**，这正是「数量一多才明显」的原因。

---

## 四、设计决策

### 4.1 做什么

| 编号 | 内容 | 依据 |
| --- | --- | --- |
| **L1** | 补两条索引（`classification_queue`、`rule_pending`） | R1，收益 20×~935×，一行 DDL |
| **L2** | Tab1 / Tab2 分页（默认每页 50 条 + 「加载更多」） | R2，HTML 10.9 MB → 约 350 KB |
| **L3** | 三面板**懒加载**：首屏只拉可见的 Tab1 | R3，省掉 22 613 个节点的首屏构建 |
| **L4** | **可见性门控刷新**：隐藏面板收到刷新事件只置脏标记，切回时才重拉 | R3，消除「Tab2 操作重拉 Tab1」的 3.3 s |
| **L5** | 宫格红点 `refresh` 去重（3 次 → 1 次） | R3，省 2 次 769 ms（补索引后约 2×5 ms） |

### 4.2 不做什么，以及为什么

| 被砍项 | 理由（实测） |
| --- | --- |
| **去 N+1**（`pending_clause_groups` / `pending_groups` 改单查询） | 补索引后 1 537 次逐条查询合计 **9 ms**，单条 JOIN 5 ms，**差值仅约 4 ms**。且分页落地后每页只查 50 条，N+1 自然消解为 50 次。改代码的收益低于回归风险，遵循 YAGNI。 |
| **编辑态懒渲染**（点击「编辑」时才生成 DOM） | 分页后每页仅 50 条，预渲染的编辑态约 150 KB / 约 12 K 节点，收益被分页吸收。若仍要减，需拆独立端点或 `<template>`，而 `<template>` 内容仍会被解析成节点，收益有限、改动大。 |
| 虚拟滚动 | 分页已足够；虚拟滚动复杂度高且与现有 HTMX swap 模型冲突。 |

> 保留一处**已知未解**：`rejected_clause_groups()` 为 0 行结果仍做两次子查询——补索引后已降至 0.3 ms，不再值得单独优化。

### 4.3 关键设计点

**分页语义**：前端维护「已加载条数」`limit`（初始 50，每次「加载更多」+50）。审核操作后**按当前已加载条数重拉**，而非回到第一页——保证用户位置不跳变，同时代价由用户自己控制。参数走 query string（`?limit=N`），后端 `LIMIT N`、`OFFSET 0`，即「取前 N 条」。不做 offset 翻页，因为审核是自上而下的流水线作业。

**刷新架构**：把刷新逻辑从各个面板模板收敛到 `review_tabs.html` 的一段常驻 script（面板被 swap 替换时不会消失），暴露 `reviewLoad(which)` / `reviewEnsure(which)` / `reviewRefresh(list)` / `reviewSwitchTab(t)` 四个函数。各操作回调不再 dispatch DOM 事件，改为直接调用 `reviewRefresh([...])`，由它按 `window.reviewTab` 决定「立即拉」还是「置脏」。

**懒加载触发点**：面板容器不再写 `hx-trigger="load"`；由 `reviewEnsure('clause')` 在首次切换/初始化时触发，之后由 `reviewLoaded` 标记去重。

---

## 五、预期效果

| 场景 | 现状 | 预期 |
| --- | --- | --- |
| 首屏进入 `/review` | 1 452 ms 后端 + 953 ms 渲染 + 22 613 个多余节点 | 约 60 ms 后端 + 约 50 ms 渲染（50 条） |
| Tab1 确认一条 | 重拉 1 501 条，约 2.4 s | 重拉 50 条，约 100 ms |
| Tab2 批准一条 | 连带重拉 Tab1 全量，约 3.3 s | 只重拉 Tab2，约 80 ms；Tab1 置脏待切回 |
| 红点刷新 | 3 × 769 ms | 1 × 约 5 ms |

---

## 六、风险与验证

| 风险 | 缓解 |
| --- | --- |
| 索引 DDL 影响既有迁移 `_migrate_rule_pending_clause_nullable`（重建表会连带删索引） | 该函数的重建路径需同步补建新索引；单列测试覆盖 |
| 分页改变审核工作流（原来一屏看全） | 默认 50 条 + 「加载更多」；已加载范围在操作后不重置 |
| 懒加载 + 门控重构可能破坏现有交互（确认/驳回/编辑/黑名单恢复） | 保留现有 `test_review_clause_panel.py` 全部断言的语义，新增结构断言；人工 QA 走 `/qa` |
| 前端逻辑无法在 pytest 执行 | 沿用项目既有做法：模板结构断言 + 浏览器实测（Playwright，隔离实例） |

**验证手段**：分页与索引走 pytest 增量测试；前端交互走隔离实例（副本库 + 独立端口）的 Playwright 实测，并复测本文件第二节的全部指标。

---

## 七、附：其它长列表界面的排查（规范 / 规则 / 词库）

审核界面的根因定位完成后，对另外三个「有长列表」的管理页做了同样的排查。

### 7.1 结论：结构健康得多，风险等级低

| 界面 | 数据量 | 端点耗时 | HTML | 查询层 | 行操作刷新 |
| --- | --- | --- | --- | --- | --- |
| 规范管理 | 5 本 | 9.2 ms | 15.1 KB | 单条 JOIN | 局部替换 |
| 规则管理 | 125 条 | 10.5 ms | 284.7 KB | 单条查询 | 局部替换（仅连带刷 1.8 KB 质量报表） |
| 词库管理 | 674 条 | 29 ms | 234.8 KB | 单条查询 | 局部替换（**但曾额外整表重拉**） |

与 `/review` 的**本质区别**：

- **增长是线性的**，审核界面是超线性的（N+1 + 全表扫描 + 隐藏面板重建叠加）。
- 查询层无 N+1、无相关子查询；`clauses.spec_id` 的 JOIN 甚至被 SQLite 的 `AUTOMATIC COVERING INDEX` 自动兜底。
- 行操作是 `hx-target="closest tr"` 的**行级局部替换**，不重拉列表——不存在审核界面那种隐藏面板交叉重建。

### 7.2 实测增长曲线（副本库模拟）

**规则列表 `/rules/list`**

| 规则数 | 耗时 | HTML |
| --- | --- | --- |
| 125（现状） | 11.6 ms | 284.7 KB |
| 500 | 28.1 ms | 1 098.2 KB |
| 1000 | 48.5 ms | 2 183.6 KB |
| 1800 | 81.8 ms | 3 924.0 KB |

**词库列表 `/lexicon/list?kind=alias`**

| 词条数 | 耗时 | HTML |
| --- | --- | --- |
| 300（现状） | 29.0 ms | 234.8 KB |
| 1000 | 86.0 ms | 789.9 KB |
| 3000 | 247.7 ms | 2 381.7 KB |
| 6000 | 497.0 ms | 4 769.4 KB |

两页现阶段**不需要分页**（做了是过度设计），触发阈值见 `TODOS.md` T30：
规则 > 800 条，或词库任一 kind > 3000 条。

### 7.3 规范页：不做分页，改限高滚动框

**问题**：当前「查看条文」「分类」填充的 `#clause-detail-area` / `#spec-class-area` 挂在列表**之后**（`specs_list.html:8-9`）。规范一多，面板被推到屏幕外，用户必须手动滚动才能看到——点了像没反应。

**两种候选方案对照实测**（浏览器渲染，真实数据）：

| 场景 | HTML | DOM 节点 | DOMContentLoaded |
| --- | --- | --- | --- |
| 条文框 887 条（**已在用、体感流畅**） | 2 362.8 KB | 11 972 | 293 ms |
| 规范列表 **50 本** | 115.5 KB | **1 077** | **20 ms** |
| 规范列表 500 本 | 1 133.1 KB | 10 527 | 232 ms |

三者强制布局均为 0.3 ms——**滚动本身与节点数几乎无关**，瓶颈只在首次解析。这正是条文框「887 条也不卡」的机制：渲染一次（293 ms，用户无感）+ 原生滚动不重渲染。

**决策：限高滚动框（方案二），不做分页（方案一）。**

- 50 本规范只有 20 ms / 1 077 节点，比已在用的条文框轻 11 倍；外推到 500 本（232 ms / 10 527 节点）仍**低于**条文框。
- 规范列表的三个行操作全是局部替换，**没有任何路径重拉整表**，「渲染一次 + 原生滚动」完全成立。
- 分页会引入跨页选择状态——用户明确否决：「按页选择意义极小，还容易漏选」。
- 方案二顺带解决问题本身：列表限高后，两个面板紧贴框下方，一屏可见；再叠加 `scrollIntoView` 自动滚入视野。

**尺寸依据**：实测表头 72.5 px、每行 74.1 px → 容纳 5 本 = **443 px ≈ 27.8 rem**。

**全选语义**：列表在滚动框内时「可见 5 本」≠「全部 50 本」，故全选框需显式 tooltip「勾选范围为全表」。因为不分页、所有行都在 DOM 里，全选天然就是全表语义，不需要额外的跨页状态——这再次说明分页方案成本更高。

### 7.4 附带发现

**词库列表双重刷新**：`lexicon_row.html` 的启停按钮既局部替换该行，又 dispatch `lexiconUpdated` 触发整表重拉（`lexicon.html:115-116` 监听）。局部替换刚完成就被覆盖，等于白做。词库 6000 条时单次重拉 497 ms / 4.8 MB。已纳入实施计划 Task 7。

**`clauses` 表无 `spec_id` 索引**：规范列表的 `LEFT JOIN clauses ON c.spec_id = s.id` 全靠 `AUTOMATIC COVERING INDEX` 临时索引兜底，**每次查询重建**，成本随条文总量线性累加。加手工索引实测 3.5 ms → 0.3 ms。已并入 Task 1。
