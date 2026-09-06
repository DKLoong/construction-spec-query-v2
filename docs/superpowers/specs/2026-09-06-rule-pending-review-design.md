# AI 沉淀规则「首审 + 驳回黑名单」设计（rule-pending）

日期：2026-09-06
状态：待评审
关联系统：施工规范智能查询平台 V2（FastAPI + HTMX + SQLite FTS5 + LanceDB）
上游：TODOS T3 的碎片治理重定向（termdict 已回滚，见 `2026-09-05-termdict-design.md` 否决注）

## 一、背景与目标

**碎片成因**（现状调查，与 termdict 同源）：
1. `apply_ai_results` 对 AI 高置信（≥ `classify.ai_confidence_threshold`）结果直接 `auto_adopted`：写条文列 + 把 `extract_keywords(content, top_n=3)` 的词**逐个 `bump_rule(new_rule_active=True)`**——首次出现的词（含 HTML/动作词碎片）未经任何人工即成为**已启用规则**并持续命中打标。
2. `process_feedback`（条文低置信人工确认）对该条文 top3 词**一次全 `bump(confirmed=1)`**——确认一条条文会顺带背书与目标无关的词（夹带）。

**目标**：AI 自动沉淀的词面规则，**首次必须人工校核**；人工认可的组合记 `approved`（此后 AI 再提免审、走半监督闭环），驳回的组合记 `rejected`（黑名单，不再沉淀，可恢复）。彻底废除「确认即整条文全背书」的批量夹带。

### 关键决策（与用户逐项确认）

| # | 决策 |
| --- | --- |
| D1 | 裁决单元 = 组合 `(clause_id, dimension, pattern, label)`（rule_pending 一行）；人工操作从不连带其它组合。 |
| D2 | 免审判定键 = 组合级 `(dimension, pattern, label)`；已背书 = `rule_pending.approved` 或 `classification_rules` 同组合 `confirmed≥1`（两条人工路径都能背书）；`rejected` = 黑名单。 |
| D3 | AI 高置信但组合首次 → **不写条文列**，插入 `rule_pending(pending)`，等人工；`auto_adopted` 的自动写列+沉淀只发生在「已背书」组合。 |
| D4 | 审核交互 = **每组合一个复选框，默认全勾 + 两个反义批量按钮**：「确认」= 勾选 approved、未勾 rejected；「驳回」= 勾选 rejected、未勾 approved。文案须明示反向语义。 |
| D5 | 三个入口共享同一对象/状态：条文审核（低置信 review）、AI 词面校核（跨条文按词聚合）、黑名单管理。 |
| D6 | `process_feedback` 废除「确认即 bump top3」：条文确认改为「写列 + 只对勾选的词组合背书」。 |
| D7 | 黑名单管理两档恢复：`rejected→pending`（恢复待审）、`rejected→approved`（批准）；不提供物理删除入口（审计保留）。 |
| D8 | UI 落点 = `/review` 页三 Tab（条文待审现有 / AI 词面校核 / 黑名单管理）。 |
| D9 | 存量 `confirmed=0` 碎片规则不动（沿用 `cleanup_rule_pollution` 清理）；机制只面向未来提案。 |
| D10 | 规则页手动建/编辑规则不受 rule_pending 约束（人始终可显式绕过，物理不锁死）。 |

> **2026-09-06 /plan-eng-review 修订（取代 D4/D5/D6 部分表述）**：
> - **Tab1 数据源 = `rule_pending` 按条文聚合**（同条文多候选标签并排，各 pending 行），`classification_queue` 降为状态/终态 + 「无 pending 词的低置信条文」兜底源（D8 中「条文待审」主源相应调整）。
> - **单条文多标签交互**：checkbox 默认全勾 + 反义批量不变，但作用对象是「标签」（非词）；「确认」=勾选 approved/未勾 rejected，「驳回」反向。
> - **驳回后条文滞留 Tab1**，标注「词已驳回，请为条文输入新标签」；行内「编辑」预填未驳回标签，删除=该标签驳回、保留=approved、**新增标签=只写列（纯打标，不沉淀规则；用户裁决：分类树按列生效故无需规则承载）**。规则沉淀只经 approved 组合/词面/规则页。编辑/保存复用规范管理页 clause 分类编辑外观；取消无副作用。
> - **词/规则沉淀出口收敛**（D6 语义扩展）：任何路径不对未选标签/词隐式沉淀；**inline 新标签不沉淀**（纯打标），沉淀只经 approved 组合/词面/规则页。

> 本修订已并入实施计划 `docs/superpowers/plans/2026-09-06-rule-pending-review.md`（Global Constraint 9-11 与 Task 5）。

### 非目标（本期不做）

- termdict 式的权威标签收口 / label 白名单校验（已否决）。
- 存量碎片规则的自动识别与批量停用（复用既有 cleanup 脚本）。
- dim2/3（无 AI 队列）、jieba userdict（术语库，另立项）。
- 批次内的策略/参数化（首次判定粒度不设可调开关，保持简单）。

## 二、使用边界（写入代码注释）

1. **rule_pending 只裁决「AI 沉淀词面规则」**：不约束条文低置信人工直接标注（写列仍允许），不约束规则页手动建规则。
2. **approved 背书在组合层**：(dim, pattern, label) 任一 approved 即该组合免审；同 pattern 不同 label 互不影响。
3. **rejected 即黑名单**：AI 再提该组合跳过（不写列、不沉淀、不重插）。恢复走黑名单管理两档。
4. **写列与沉淀分离**：条文打标（`clauses.{dim}_*`）只在「组合已背书（auto）或人工确认该条文」时发生；首次/被驳组合不写列。
5. 所有状态变更（pending→approved/rejected、restore/批准）记 `log_action`（commit 后，category=classify 或 review），供审计与误操作溯源。
6. 幂等：同 `(clause_id, dimension, pattern, label, status=pending)` 不重插；判定与插入在写连接事务内。

## 三、现状与改动落点

| 现状 | 本次改动 |
| --- | --- |
| `batch_queue.apply_ai_results`：conf≥阈值 → 全 auto 写列 + 逐 kw bump 启用 | auto 分支按组合裁决（背书→照旧 / 首次→pending 不写列 / 驳回→跳过），见 §五 |
| `feedback.process_feedback`：写列 + extract top3 全 bump(confirmed=1) | 改为「写列 + 只对勾选组合 approved」（见 §七）；不再整条文隐式背书 |
| review 队列 / `/review` 页（rules_routes 内）单列表 | 三 Tab：条文待审 / AI 词面校核 / 黑名单管理（见 §六） |
| `rules_routes` review_page/_fetch_review_items | 扩展聚合查询与 decide 端点 |
| 无待审对象表 | 新增 `rule_pending`（见 §四） |

## 四、数据模型

`rule_pending` 追加进 `SCHEMA_SQL`（幂等）：

```sql
CREATE TABLE IF NOT EXISTS rule_pending (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension     TEXT NOT NULL,            -- dim4/5/6
    pattern       TEXT NOT NULL,            -- 词面（AI 提案词）
    label         TEXT NOT NULL,            -- AI 提议标签
    clause_id     INTEGER NOT NULL,         -- 来源条文（供确认回填/审计）
    ai_confidence REAL,
    batch_id      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    updated_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_rule_pending_key
    ON rule_pending(dimension, pattern, label, status);
CREATE INDEX IF NOT EXISTS idx_rule_pending_status ON rule_pending(status);
```

- 一行 = 一次「条文×组合」提案（仿 classification_queue 的来源可重复性）；**幂等键 `(clause_id, dimension, pattern, label)` + status='pending'**（同源重复提案不重插，新条文命中同组合新增一行，供多条文回填）。
- approved/rejected 用状态表达，不物理删除；同 (clause,组合) 被驳后再批准走黑名单管理或条文人工（保留一行终态，审计友好）。

## 五、AI 采纳裁决流（改 `apply_ai_results`）

`auto` 分支（conf≥阈值，结果在队列维度 dim4/5/6）逐 kw（AI 提案词 = `extract_keywords(content, top_n=3)`）判定：

```text
f(组合 key=(dim, kw, ai_label)):
  ∃ approved（rule_pending.approved 或 rules.confirmed≥1 同键） → auto：写列 + bump(hit++)   # 免审
  ∃ rejected（rule_pending.rejected）→ 跳过（不写列/不沉淀/不重插）                          # 黑名单
  否则（首次）→ 插入 rule_pending(pending, clause_id, conf, batch)；条文不写列              # 等人工
```

- 同批/同连接内一次性取 key 集做三态查询（不进条文内层循环逐查）。
- **首次词的队列归属（定稿）**：conf≥阈值但组合首次 → 该 `classification_queue` 行**不置 `auto_adopted`**，置 `status='review'`（在 Tab1 展示，条目注明「词首审」），同时词入 `rule_pending(pending)`（在 Tab2 展示）。两个 Tab 共享同一组 rule_pending 行：任一处对组合批准都写列回填 + 背书；任一处驳回都黑名。条文列留空，待 approved 后回填。

## 六、审核交互（/review 三 Tab）

### Tab1 条文待审（低置信，沿用）
改造确认动作：渲染每条待审条文时显示「候选词」区 = 该条文 AI 关联的未决词组合（conf≥ 阈值转来的首次词 + 低置信的词），每组合一个复选框默认全勾 + 「确认 / 驳回」双按钮（D4 反义语义）。确认 → 写列 + 勾选组合 approved、未勾 rejected；驳回 → 未勾 approved、勾选 rejected。

### Tab2 AI 词面校核（新）
聚合查询 `status='pending'` 按 `(dimension, pattern)` 分组，展示每词面的候选标签组（各带来源条文数、平均 conf）与整组复选框（默认全勾）。行内双按钮同 Tab1 反义语义；一次操作影响该词面该分组的所有 pending 行（勾选集合 approved / rejected 各按动作）。

### Tab3 黑名单管理（新）
列出 `status='rejected'` 组合（词面/标签/驳回次数/最近驳回时间），每行「恢复待审」「批准」两钮（D7）。

### 交互与数据流公共要点
- 列表刷新沿用 `HX-Trigger` 模式（review 页既有 `hx-on` / 事件链）；三个 Tab 数据源分离但同写 rule_pending。
- 「确认」按钮文案下必有反向语义小字（D4 防反直觉）：如「未勾选的将自动驳回」。
- 计数徽标：Tab2 显示 pending 词面数；rejected 计数仅在 Tab3。
- 端点：POST `/review/pending/{action}`（action=approve|reject），body 传勾选 id 集 + 作用域（词面组/条文组），服务端取作用域全集做正反分配；复用现有 review 路由文件。

## 七、`process_feedback` 语义改造

- 保留：queue→done、`clauses.{dim}_*=confirmed_label` 写列。
- 废除：对 content top3 逐词 `bump(is_confirmed=True)`。
- 改为：对调用方（review 前端）显式勾选的词组合（若无现成 pending 行则直接建 approved 行，人工显式背书免去先 pending）执行 `bump(is_confirmed=True, label=confirmed_label)`；未勾选词**不沉淀**。
- 兼容：rules_routes 的 confirm/batch_confirm 需改为携带勾选 ids（无勾选则仅写列不沉淀——纯打标仍允许，语义 D6）。

## 八、错误处理 / 一致性 / 并发

| 场景 | 行为 |
| --- | --- |
| pending 不存在于作用域（并发已处理） | 幂等：操作按现存行执行；空作用域 no-op 返回计数 |
| 同 (clause,组合) 并发重复提案 | 唯一/pending 判重防止重复插；判定与插在写连接事务内 |
| 裁决已 approved 又 reject | 允许（人工新决定）；黑名单管理可恢复 |
| 缓存 | 规则/候选无持久 TTL 缓存（实时查询），无需 invalidate；`hybrid_search` 结果缓存与规则无关不清 |
| 审计 | 每次状态变更 `log_action`（commit 后调用，带操作者 username） |

## 九、测试（TDD，预估 ~40 例）

- `test_rule_pending.py`：表/幂等/三态查询/索引；
- 裁决流：背书→auto 写列+沉淀、首次→pending 不写列、驳回→跳过不写列（各走 apply_ai_results 批次）；
- 词面校核反义批量：勾选+确认 → 勾的 approved / 未勾 rejected；勾选+驳回 → 反转；跨条文回填正确；
- process_feedback 改造：勾选词沉淀、未勾不沉淀、纯打标无词；
- 黑名单管理：恢复待审 / 批准状态迁移 + 免审键生效；
- UI headless：三 Tab 冒烟（一次性脚本）；
- 回归适配（预期破坏点）：`test_batch_queue`/`test_rule_feedback_loop`/`test_classifier_ai`/`test_logs`（auto 语义变更），`test_import`/`test_review_batch`（确认流程变更）。

## 十、风险 / 开放项

- **反向按钮误操作** → 靠黑名单管理恢复 + 审计兜底（D7）。
- **低置信条文候选词的衔接（已定）**：低置信（conf<阈值）条文不额外插 rule_pending；Tab1 确认时对其显式勾选的词组合直接 approved 背书（§七），未勾不沉淀。
- **首次词双 Tab 双入口**：Tab1（条文，因置 review）与 Tab2（词面）对同一组 pending 行操作，approved/rejected 共享、写列回填幂等——验收需重点核对两个入口状态一致（词面批准后回填列，Tab1 该条文仍会显示待确认；若已回填需避免重复/矛盾，plan 明确 Tab1 对已回填条文的展示）。
- 全量回归面较大：auto/confirm 语义双变，需 Task 化分步验证（679 → 预期有波动，逐批适配）。
