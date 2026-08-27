# 施工规范查询系统 V2 — 知识库维护功能设计

> 日期：2026-08-27
> 范围：四大块——① 规范生命周期管理 ② 维护工具（健康检查/导出/备份） ③ 复核减负（半监督闭环/批量操作） ④ 日志管理
> 状态：已与用户逐项确认定稿

---

## 1. 背景与目标

系统将分享给多人使用，缺少日志跟踪导致问题难定位；工程规范会更新/废止，库内混入作废规范会造成严重误导；人工复核量大。本次新增知识库维护功能，覆盖四大块，并按 **生命周期 → 维护工具 → 复核减负 → 日志界面** 顺序推进。

## 2. 已确认决策记录

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 生命周期状态落点 | **复用现有 `specifications.status` 字段**，不新增 dim1 validity 子字段 |
| D2 | 实施顺序 | 生命周期(P0) → 维护工具(P1) → 复核减负(P2) → 日志界面(P3) |
| D3 | 状态呈现方式 | 检索/QA 用**"仅现行"+"修订中"两个复选框并排一行**（不进分类树），组合决定状态过滤 |
| D4 | 分类树多选 | **全局同维多选**升级（filters 从标量变数组），非仅状态层 |
| D5 | 导入版本校验 | 填完编号/名称防抖自动校验 + 实时可编辑标签（下拉切换） |
| D6 | 校验兜底 | AI 不可用/超时 → 默认"现行"+ ⚠️提示人工确认；规范管理页提供状态修改入口 |
| D7 | 替代关系来源 | AI 识别被替代编号 + 人工可改（导入界面输入框 + 规范页维护） |
| D8 | 提示语范围 | 条文详情弹窗底部 + 检索结果列表标注 + QA 输出指引（三处全做） |
| D9 | 多版本共存 | 仅 schema 预留（同 code 多行共存 + replace_by 字段），本期不做版本对比 UI |
| D10 | 导出格式 | 规则/复核队列导出 **JSON** |
| D11 | FTS optimize | 启动时异步执行一次 + 维护界面手动按钮 |
| D12 | 健康检查结果 | 写 `system_logs` + 独立快照表持久化 |
| D13 | 批量重分类范围 | **可选**（全部 / 仅未分类，默认仅未分类） |
| D14 | 规则质量报表 | 规则管理页顶部**实时展示**（非定时），附导出按钮 |
| D15 | 日志保留 | `system_logs` 保留 90 天 + 手动清理 |
| D16 | 日志埋点范围 | 全量行为埋点（导入/规范/条文/规则/分类/复核/QA/健康/导出/登录等） |
| D17 | 维护按钮布局 | 左侧功能区四宫格扩展（规范/规则/审核/同义词/维护/AI问答） |
| D18 | 修订中语义 | **"仅现行"严格放行 `status='现行'`**；修订中由独立复选框控制 |

---

## 3. 数据模型变更

### 3.1 `specifications`（改表，幂等迁移）

- `status TEXT DEFAULT '现行'`（**已有**，值域规范化为：`现行` / `废止` / `修订中`）
- 新增 `replace_by_spec_id INTEGER REFERENCES specifications(id)` —— 本规范被哪本新规范替代（被替代者为废止，此字段非空）
- 新增 `spec_version TEXT` —— 版本标识（如 `2015`），为版本对比预留（D9），本期不参与任何逻辑
- 迁移：`ALTER TABLE` try/except 幂等（沿用现有 `file_hash` / `clause_is_non` 迁移模式）
- `code` **不加唯一约束**（已天然支持同编号多版本共存）

### 3.2 `system_logs`（新表）

```sql
CREATE TABLE IF NOT EXISTS system_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,      -- import/spec/clause/rule/classify/review/qa/maintenance/auth/system
    level       TEXT NOT NULL,      -- INFO / WARN / ERROR
    action      TEXT NOT NULL,      -- 具体动作，如 导入规范 / 删除条文 / 批量确认
    detail      TEXT,               -- JSON 附加信息（对象/条文/规则/异常堆栈等）
    username    TEXT,               -- 操作者（系统任务为 'system'）
    duration_ms INTEGER,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_system_logs_category ON system_logs(category);
CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs(created_at);
```

### 3.3 `health_check_snapshots`（新表）

```sql
CREATE TABLE IF NOT EXISTS health_check_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    result      TEXT NOT NULL,      -- JSON：各检查项 {name, count, severity, fixable}
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
```

### 3.4 现有表

`qa_request_logs` 沿用（QA 日志独立存储），`classification_queue` 沿用（status 已含 `auto_adopted`）。`classification_rules` 沿用（字段已含 `hit_count`/`confirmed`/`is_active`）。

---

## 4. P0 · 规范生命周期管理

### 4.1 导入自动打标（含人工修改入口）

**前端（`import.js` + `tree_panel.html` 导入对话框）**
- `code` / `title` 输入防抖（800ms）→ `GET /import/validate-version?code=&title=`
- 响应渲染在导入框下方：
  - 状态标签下拉（`现行`/`废止`/`修订中`），AI 结果作为默认选中值，**可点击切换**
  - "被替代编号"输入框，AI 识别结果作默认值（可改）
- AI 不可用/超时 → 默认"现行"，标签旁显示 ⚠️「AI 校验不可用，请手动确认状态」
- 提交导入时携带 `status` + `replaced_by_code` 字段

**后端（`import_routes.py`）**
- 新路由 `GET /import/validate-version`：复用 AI 分类后端（`app.ai.cli_client.get_backend` / `classifier_ai` 同链路），prompt 判断 `{status, replaced_by_code}`；后端不可用/异常 → 返回 `{status:'现行', ai_available:false}`（不抛错）
- 导入入库（`POST /import/...` 现有流程）：写 `specifications.status` = 界面最终选定值
- **反向联动**：若 `replaced_by_code` 命中库中已有规范（按 code 匹配）→ 反写该旧规范 `status='废止'` + `replace_by_spec_id=新规范id`；无命中则跳过（后续可通过规范页手动设置）。联动操作记入 `system_logs`

### 4.2 规范状态管理入口（历史存量统一维护）

- `specs_table.html` 新增"状态"列：三色标签（现行=绿 / 废止=红 / 修订中=橙）
- 行内状态切换：下拉 select → `PUT /specs/{id}/status`（新路由），改完即时生效
- 该入口同时是 AI 校验兜底的人工修改路径（D6）

### 4.3 检索过滤

**前端**
- 搜索框下方新增一行（与"包含前言·条文说明"、"启用 CE 精排"同区）：`☑ 仅现行` + `☐ 修订中` 两个复选框**并排一行**
- `searchState` store 增加 `statusCurrent: true`（默认勾选）、`statusRevising: false`
- 组合语义：
  - 仅现行勾选 → 过滤 `status IN ('现行')`
  - 仅现行+修订中 → `status IN ('现行','修订中')`
  - 仅修订中 → `status IN ('修订中')`
  - 两者均不勾 → **不过滤状态** + 顶部轻提示 4s「注意：当前展示结果未过滤非现行规范」
- 请求参数：`status_filter=current,revising`（逗号分隔，后端解析）
- **分类树全局同维多选**（D4）：
  - `tree.js` `selectFilter` 改为数组（同维点选 toggle 进/出数组），`activeFilters[dim]` 从标量变数组
  - `dispatchSearch` 对数组维度输出多值参数（如 `dim4_specialty=钢筋&dim4_specialty=混凝土`）
  - `tree-panel.html` 节点 `active` class 判定改为 `includes(value)`

**后端（`hybrid_search.py` / `sql_search.py`）**
- 新增可选参数 `status_filter`，解析为 `status IN (...)` 追加到 WHERE（现有 SQL 已 `SELECT s.status as spec_status`，仅加条件）
- 参数为空/未传 → 不追加条件（兼容旧请求，多选升级不破坏单值参数）

### 4.4 QA 过滤与废止高亮

- `qa_panel.html` 增加"仅现行/修订中"复选框（默认仅现行），参数覆盖现有 `include_invalid` + `status_allow`
- `qa_routes.py` / `qa/config.py`：`status_allow` 允许 UI 传值覆盖（现从 settings 读取，需支持请求级覆盖）；两者均不勾 → `include_invalid=True`
- **输出高亮**（`qa.js` 渲染）：
  - 来源为废止规范 → 来源标注 ⚠️「本规范已废止，请查阅新版规范」红色警告
  - 来源为被替代规范（`replace_by_spec_id` 非空）→ ⚠️「本规范已被《新编号 新名称》替代，请以新规范为准」
  - 来源提取沿用现有 `context.picked` 链路，需在候选 dict 中带出 `spec_status`（已有）与 `replace_by` 信息

### 4.5 替代提示语（三处，D8）

1. **条文详情弹窗底部**（`clause_detail.html` footer 下）：`spec_status='废止'` 或 `replace_by_spec_id` 非空时，底部红框提示「本规范已被《xxx》替代，请以新规范的规定为准」；`spec_status='废止'` 时附加废止提示。clause-detail 查询需 JOIN 带出被替代规范编号与名称
2. **检索结果列表**（`result_list.html`）：来源行标注小标签（废止=红「已废止」/ 被替代=橙「已被替代」）
3. **QA 输出指引**：见 4.4

### 4.6 多版本共存预留（D9）

仅完成：`spec_version` 字段 + `code` 非唯一 + `replace_by` 字段。版本对比 UI 本期不做。

---

## 5. P1 · 维护工具

维护界面为 `/maintenance` 页面（`base.html` center_content），三个 Tab：健康检查 / 导出备份 / 日志管理（日志 Tab 属 P3，本期先占位）。

### 5.1 健康检查

新路由 `POST /maintenance/health-check`，检查项与修复动作：

| 检查项 | 判定规则 | 修复动作 |
|---|---|---|
| 孤立条文 | `parent_clause` 非空且指向的 id 不存在 | 置 `parent_clause=NULL` |
| 空内容条文 | `content` 为空/全空白 | 仅报告（列清单），不自动删 |
| 分类标签异常 | `ai_classified=1` 且 `dim4/dim5/dim6` 全空 | 置 `ai_classified=0, needs_review=1`（重新进复核） |
| 向量不一致 | LanceDB 有而 SQLite 无 / SQLite 有而 LanceDB 无 | 删孤儿向量（复用 `VectorStore.sync_with_db`）+ 补齐缺失索引 |
| FTS 不一致 | `clauses_fts` rowid 与 `clauses` 对不上 | 重建 FTS（全量回填 `search_text`） |

- 结果：`{checks:[{name, count, severity, fixable}]}`，前端分项展示（问题数 + 严重程度）
- 持久化（D12）：写 `system_logs` + `health_check_snapshots`（存结果 JSON）
- 操作：每项「修复」按钮 + 一键「修复全部」；独立「重建向量索引」按钮（全量重索引）
- 修复操作均记 `system_logs`

### 5.2 导出 / 备份

| 功能 | 实现 | 路由 |
|---|---|---|
| SQLite 备份 | `VACUUM INTO data/backups/spec_query_YYYYMMDD_HHMMSS.db`（带时间戳） | `POST /maintenance/backup` |
| 导出全部分类规则 | `classification_rules` 全量 → JSON 文件下载 | `GET /maintenance/export/rules` |
| 导出复核队列 | `classification_queue` 中 `status='review'` + 条文内容/AI 标签/置信度 → JSON 下载 | `GET /maintenance/export/review-queue` |

- 备份目录 `data/backups/` 由 config 增加路径常量，启动时确保存在
- 导出文件响应为附件下载（`Content-Disposition`）

### 5.3 FTS5 optimize

- 启动时后台线程执行 `INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')`（异步，不阻塞启动）
- 维护界面按钮 `POST /maintenance/fts-optimize` 手动触发（D11）

---

## 6. P2 · 复核减负

### 6.1 半监督闭环（补断点）

现状断点：`apply_ai_results` 中 `auto_adopted`（conf≥0.7）直接写 `clauses` 分类列，**不沉淀规则**；人工确认才走 `process_feedback` 沉淀规则（新规则 `is_active=0`）。

**改造 `batch_queue.apply_ai_results`**：
- `auto_adopted` 分支：写分类列后，调用规则沉淀（提取关键词 → 更新命中/生成规则），**新生成规则 `is_active=1` 直接启用**（来源为 AI 高置信自动采纳，无需人工复核）

**改造 `feedback.process_feedback`**：
- 新增**自动启用**逻辑：规则 `confirmed/hit ≥ 0.8` 且 `hit ≥ 5` → 置 `is_active=1`（现有仅"低正确率自动停用"：`confirmed/hit < 0.3 and hit > 10`）
- 新增参数 `source_conf`（来源 AI 置信度）：人工确认来源且 `source_conf ≥ 0.9` 时新生成规则 `is_active=1`；否则 `is_active=0` 待审核（低置信度保留待审核，符合需求）
- 常量入 `app/config.py`：`RULE_AUTO_ENABLE_RATIO=0.8`、`RULE_AUTO_ENABLE_MIN_HIT=5`、`RULE_AUTO_ENABLE_CONF=0.9`

### 6.2 规则质量报表（规则页实时，D14）

`rules_list.html` 顶部扩展（现 `rules-stats` 面板下）三类异常标记（实时 SQL 计算）：
- 🔔 **建议启用**：`is_active=0` 且 `hit_count≥5` 且 `confirmed/hit_count≥0.8`
- ⚠️ **建议停用**：`is_active=1` 且 `hit_count>10` 且 `confirmed/hit_count<0.3`
- 🧟 **僵尸规则**：`hit_count=0` 且 `created_at < 当前-30天`
- 每类带「全部启用/停用/删除」一键按钮 + 列表内行级快捷操作
- 「导出报表」按钮 → JSON 下载

### 6.3 批量操作 UI

**复核队列（`review_list.html`）**
- 每行加 checkbox + 表头全选；工具栏「批量确认」「批量驳回」（带 `hx-confirm` 防误触）
- 新路由 `POST /review/batch-confirm`、`POST /review/batch-reject`（接收 queue_id 列表，批量调用现有 confirm/reject 逻辑）

**批量重分类（`specs_table.html`）**
- 每行 checkbox + 表头全选 + 工具栏「重新分类所选」
- 范围选择（D13）：`全部条文` / `仅未分类条文`（默认仅未分类）
- 新路由 `POST /specs/batch-reclassify`（`{spec_ids, scope}`）：
  - 范围逻辑：所选规范下，scope=all → 全部条文；scope=unclassified → `ai_classified=0` 或 `needs_review=1` 的条文
  - 重置 `ai_classified=0`、清队列、`classification_queue` 插 `pending` → 触发 `process_pending_batches` 异步跑分类
  - **scope=all 时先清空 dim4/5/6 再重跑**；scope=unclassified 不动已有标签

**规则页批量处理**：见 6.2 一键按钮

---

## 7. P3 · 日志管理

### 7.1 埋点工具

- 新增 `app/logging_util.py`：`log_action(category, level, action, detail=None, duration_ms=None, username=None)` → 写 `system_logs`
- 路由埋点清单（D16 全量）：
  - `import`：导入成功/失败、版本校验结果
  - `spec`：新增/删除规范、改状态、改分类
  - `clause`：新增/删除/编辑条文
  - `rule`：增删改规则、启停、批量处理
  - `classify`：AI 分类运行、批量重分类
  - `review`：确认/驳回/批量确认/批量驳回
  - `qa`：沿用 `qa_request_logs`（不写 system_logs）
  - `maintenance`：健康检查、修复、导出、备份、FTS optimize
  - `auth`：登录成功/失败
  - `system`：启动/关闭、日志清理
- 现有 `logger`（Python logging）保留，`log_action` 为 DB 结构化记录，二者并存

### 7.2 日志界面

- `/maintenance` 页「日志管理」Tab：
  - 筛选：分类（下拉）、等级（含「⚠️ 异常」= ERROR+WARN 一键标签）、操作者、时间范围、关键词
  - 列表分页（如每页 50），详情行内展开（detail JSON 格式化展示）
  - **一键导出异常日志**（JSON）：导出当前筛选结果（默认全部 ERROR+WARN）
- QA 日志独立 Tab：查询 `qa_request_logs`，只读展示

### 7.3 保留策略（D15）

- 默认保留 90 天：启动时后台清理 `created_at < 当前-90天` 的 `system_logs`
- 维护界面「手动清理」按钮（可选择清理范围：全部日志 / 90 天前 / 指定分类）

---

## 8. 实施顺序与依赖

1. **P0 生命周期**（D2 第一优先，防废止规范误导）
   - 依赖：`specifications` 迁移（replace_by_spec_id/spec_version）→ 导入打标 → 规范页状态列 → 检索复选框+多选 → QA 过滤/高亮 → 替代提示语三处
2. **P1 维护工具**
   - 依赖：`system_logs`/`health_check_snapshots` 建表（日志表 P1 先建，P3 界面用）→ `/maintenance` 页面骨架 + 健康检查 → 导出备份 → FTS optimize
3. **P2 复核减负**
   - 依赖：`apply_ai_results`/`feedback` 改造 → 规则报表 → 批量 UI
4. **P3 日志界面**
   - 依赖：`system_logs` 已建 + 埋点已铺 → 日志 Tab + QA 日志 Tab + 保留清理

## 9. 测试策略（TDD）

新增测试文件（遵循 CLAUDE.md 1.4：先测试后实现，覆盖正常/边界/异常）：

| 文件 | 覆盖 |
|---|---|
| `tests/test_spec_lifecycle.py` | 导入打标（AI 可用/不可用/超时）、反向联动、状态列修改、检索 status_filter 组合（4 种）、QA 高亮、替代提示语三处 |
| `tests/test_search_multiselect.py` | 分类树多值参数解析、旧单值参数兼容 |
| `tests/test_maintenance.py` | 健康检查 5 项判定+修复、备份、导出 JSON、FTS optimize、快照写入 |
| `tests/test_rule_quality.py` | auto_adopted 沉淀规则、自动启用阈值、报表三类异常计算 |
| `tests/test_batch_review.py` | 批量确认/驳回、批量重分类范围两档 |
| `tests/test_logs.py` | 埋点写入、90 天清理、异常导出 |

## 10. 风险与注意事项

| 风险 | 应对 |
|---|---|
| AI 版本校验误判（打标错误） | 导入标签可编辑 + 规范页状态列人工修改兜底；校验失败默认现行不拦截导入 |
| 分类树多选升级破坏现有交互 | 后端兼容单值/多值参数；前端 active 判定改 includes；回归现有搜索/QA 用例 |
| 批量重分类覆盖人工标签 | 默认 scope=unclassified 不动已确认标签；scope=all 需二次确认 |
| auto_adopted 规则自动启用可能引入噪声 | 阈值可配置（config）；报表「建议停用」可批量回退；低正确率自动停用逻辑保留 |
| 日志膨胀拖库 | 90 天自动清理 + 手动清理；`qa_request_logs` 独立且仅 QA 写 |
| 删除被替代规范 | `replace_by_spec_id` 引用置空（防悬挂），删除逻辑补该处理 |
| 服务重启双进程 | 按项目 CLAUDE.md 第三节流程执行（杀全部残留 → 验证端口 → 启动 → 复验唯一监听） |

## 11. 未纳入本期

- 规范版本条文级差异对比 UI（D9 仅 schema 预留）
- 规则质量报表定时快照（D14 改实时展示）
- 日志邮件/Webhook 告警
