# Tab1/Tab2 打标-沉淀解耦（词面收敛）设计

日期：2026-09-06
状态：待评审（v2 收窄版：不合并低置信块，只做主表语义改造）
关联系统：施工规范智能查询平台 V2（FastAPI + SQLite FTS5 + LanceDB）
上游：`2026-09-06-rule-pending-review-design.md`（AI 沉淀规则「首审+黑名单」）落地后的人工量治理；对齐用户 Q3/Q4 观察（删除重导 JGJ107 → 71 条 178 项 → 数页待审）。

## 一、背景与问题（实证）

**现状数据流**（rule-pending 裁决，已核实）：
- `apply_ai_results`：AI 对每个「条文×维度」给 1 个 label 后，`extract_keywords(content, top_n=3)` 把 **top3 关键词各建一条 pending** `(clause_id, dimension, pattern=词, label)`（`batch_queue.py:108`）。
- dev 实证：71 条文 → 178 个「条文×维度」pending 组，**173 组恰好 3 个候选、178/178 组内部 label 全相同**（同 label、不同词面）。529 行 pending。
- 词面批准（Tab1/Tab2）`approve_rule` 会把该词面写成规则，供未来导入**确定性自动分类**（`classify_clause`，`import_routes.py:419`）。
- Tab1 现状 = **主表（`pending_clause_groups` + `rejected_clause_groups`，有 pending/已驳词的条文）+ 底部低置信兜底块（`review_list.html`，queue review 且无 pending/rejected 关联词）**，两块并排、两套渲染、两套确认端点。

**问题**：
1. **「条文打标」与「词面沉淀」两件事被绑在同一批 checkbox 上**。Tab1 主表批准一个条文 = 展开 3 个词 `resolve_keys → approve_rule`，为同一 label **同时建 3 条规则**（含 `规范/混凝土` 碎片）→ 碎片污染复发路径。
2. **Tab1 视觉噪音**：同一条文同 label 的 3 个词 chip 长得一样（用户 Q3 问「为何每条 3 个源」）——chip 是 3 个待背书规则词，不是 3 个来源。
3. **Tab1 主表 `pending_clause_groups`（按「有 pending 词」聚合）**会把「已 auto_adopted 但有残留词」的条文也带进 Tab1（实测 5 组），与「条文待审」语义不符。
4. 人工量大（用户 Q4）：529 全量 pending，但唯一需人工背书的组合仅 201 个（distinct dim+pattern+label）。词面聚合（Tab2）是批量杠杆，应为主链。

**本 spec 只做语义/交互收敛（①），范围收窄（v2）**：不动低置信兜底块与旧端点，只改主表批准语义 + 修 auto_adopted 误入。③ prompt+候选隔离、④ G1/G2/G3 护栏回测另立 spec；rule_pending 表结构不动。

## 二、核心决策（与用户逐点确认）

| # | 决策 |
| --- | --- |
| C1 | **打标与沉淀解耦**：Tab1 主表批准条文 = 只写分类列 + queue `review→done`，**不 set 词状态、不 approve_rule**。词面沉淀只经 Tab2 一个出口。 |
| C2 | **词面池来源 = 维持现状「先提词」主链**（AI 提案词全进池，Tab2 按词聚合）。不做「打标后提词」——会丢掉「批一个词面=反联批量打标」的聚合杠杆。 |
| C3 | **Tab1 主表词 chip checkbox 保留，语义 = 去勾即驳回（黑名单）**：勾选（默认全勾）= 该词保留为 Tab2 候选（pending 不动）；去勾/× = 该词 `rejected`。**不标 approved**。 |
| C4 | **删 Tab1 主表「批量驳回」按钮**：标签不认 → ✏️ 编辑输新标签覆盖；词不认 → 去勾/×（黑名单）。 |
| C5 | **Tab1 主表沿用 `pending_clause_groups` + `rejected_clause_groups`（有 pending/已驳词的条文），不合并低置信块**；查询排除「queue 已是终态（done/auto_adopted/rejected）」的条文，修 auto_adopted 残留词误入。低置信兜底块（无词条文）原样保留在 Tab1 底部。 |
| C6 | **Tab1 = 词面聚合未覆盖的条文补充视角（主表）+ 低置信兜底**；Tab2 = 词面聚合主链。两者共享同一批 queue/pending 对象，任一入口完成即推进。 |
| C7 | **词 chip「同 label 3 词」UI 保留**（用户判定有助于黑名单精修），不改展示形态。 |
| C8 | **反联守卫**：`backfill_and_close` 只写 `queue status='review'` 的条文，Tab1 已定案/改标条文绝不被词面反嚼覆写。 |
| C9 | **inline 新标签 ≠ 原 ai_label → 驳回该条残留 pending 词**（一致性规则，防 Tab2 之后批准旧词面回头覆写人工新标签）。 |
| C10 | **免审链保留**：词面已背书（approved 或规则 confirmed>0 且 active）→ AI 再提直接 `auto_adopted`（写列+dump），不进 Tab1。 |

### 范围边界（v2 收窄，明确不做）
- **不合并低置信兜底块**：`review_list.html`、`/review/list`、单条 `/review/{qid}/confirm|reject`、`batch-confirm`/`batch-reject` **保留不删**；仅把低置信 confirm 里「勾选词背书成规则」的旧夹带去掉（词面沉淀收敛 Tab2 后，低置信不再产生词面背书；见 C1 延伸）。`queue.status='rejected'` 语义不动（reject 端点仍可把 queue 置 rejected 作跳过，但不再反向批词——低置信 confirm/reject 不触碰 rule_pending）。
- 不做「三源合一 / 大总览重构」；不做低置信数据源并表。
- ③④ 另 spec；rule_pending / classification_queue 表结构不变。

### 评审修订（2026-09-06 /plan-eng-review + codex outside voice 采纳）

| # | 决策（评审补） |
| --- | --- |
| C11 | **前置条件**：目标库须已清到「locked 种子基准」（dim4/5/6 active=21/23/23，无 confirmed 碎片；dev 已在 9-06 清过）。旧 confirmed>0 碎片是设计信任标记，**不被**词面驳回停用（`deactivate_fragment` 只停 confirmed=0 是有意的）；本 spec 不改存量，只防新污染。 |
| C12 | **表述收窄**：「词面沉淀只经 Tab2 一个出口」实际含两类**既定例外**——① 黑名单 approve（Tab3 两档恢复的 approve）仍直接 `approve_rule`；② auto 分支对已背书词仍 bump（C10）。故准确表述为「**新词面的普通沉淀只经 Tab2**」；低置信条文确认=纯打标，其内容词不入池（这些词从未进 rule_pending，删 patterns 背书不造成词面丢失）。 |
| C13 | **维度收口**：`decide approve` 的 dimension **必须由 body 显式传入**——缺失 / 非字符串 / 不在 dim4/5/6 白名单内一律 400，**无任何回退**（Tab1 前端已按分组实参必传；测试种子须显式带 dimension）。`inline_edit` 优先取 body 显式值，缺失（None）时仅回退「该 (clause_id) 唯一 review queue 的 dimension」，仍歧义才 400。**废弃**从任意 pending id 反查维度的兜底（防跨 dim4/5/6 条文错维写列/错维驳词）。 |
| C14 | **并发守卫落地**：`_confirm_clause` 与 `_backfill_by_status` 用**条件更新**——clause 列 UPDATE 带 `AND EXISTS(SELECT 1 FROM classification_queue WHERE clause_id=? AND dimension=? AND status='review')`，命中才置 queue done；未命中（已被并发 done/改标）no-op。**落地形态**：`_confirm_clause` = 单条 UPDATE + rowcount → `bool`（False 即 no-op）；`_backfill_by_status` = **单条批量条件 UPDATE**（`id IN (...)` 列表 + EXISTS，rowcount 即实际写列条文数）后跟一条批量 `queue review→done`，**不在循环内逐条 UPDATE/提交**（项目规则 1.3）。两者共同构成 spec §七「先到者生效，后到者 no-op」。 |
| C15 | **低置信端点守卫**：`confirm_review`/`reject_review` 的 queue 查询加 `AND status='review'`；`process_feedback` 写列前确认 queue 仍 review（无 review 行 → no-op 不覆写），闭环 C8「已定案/改标条文不被覆写」。 |
| C16 | **计数口径同步**：`pending_counts()` 的 clause 计数改为 = `pending_clause_groups()` + `rejected_clause_groups()` + 低置信兜底（与 Tab1 新数据源同口径），Tab1 已确认（queue done）条文其残留 pending 词不再计入红点「条文待审」。 |
| C17 | **无 queue 行统一排除**：`pending_clause_groups` 排除「无 review queue 行」的 pending 条文（与 decide 400 语义一致）；既有测试种子若直插 pending 无 queue，须补 queue 或改断言。真实主链 `insert_pending` 必伴随 queue review，无 queue 残留属脏数据。 |
| C18 | **存量清理明确不做**（同 C11）：locked 基准后的存量 confirmed 碎片留给后续维护/规则页手动处理，不入本 spec Task。 |

## 三、数据流

```text
AI 分类批 apply_ai_results
  ├─ 词已背书(approved 或规则 confirmed>0 且 is_active) → queue auto_adopted：
  │    直接写列 + bump(命中)，不进 Tab1（C10）
  └─ 词首见(none) → 各词 insert_pending + queue 留 review（进 Tab1 主表 + Tab2 池）

Tab1 主表（pending_clause_groups + rejected_clause_groups，C5 加终态排除）
  ├─ ✅ 确认标签 = 写 ai_label 列 + queue done；勾选词留 pending(→Tab2)、去勾词 rejected（C1/C3）
  ├─ ✏️ 编辑/新标签 = 写新 label + queue done + 驳回残留 pending 词（C9）
  ├─ 词 chip × = 单词 rejected（黑名单）（C3）
  └─ （已删除：主表「批量驳回」按钮 C4）

Tab1 底部低置信兜底块（review_list.html，保留）：confirm = 纯打标写列（去词背书）
  └─ 不再对勾选词 approve_rule（词面沉淀仅 Tab2）

Tab2（pending_groups 按 dim+pattern 聚合，沉淀主链）
  ├─ 批准词面 = approve_rule(规则学习，唯一沉淀出口) + backfill 反联 review 条文写列 done（C8）
  └─ 驳回词面 = rejected + deactivate_fragment(停用 confirmed=0 碎片)，不写列

黑名单管理（不变）：restore(rejected→pending) / approve(rejected→approved + 反联)
```

## 四、一致性守卫（核心新增）

1. **反联守卫（C8）**：`_backfill_by_status` 写列时须确认该条 `queue.status='review'`（或等价：clauses 列未被人工改标）。已 done / 已改标的来源条文跳过写列，仅统计。`_backfill_by_status` 先按 (dimension, pattern, label, status) 取 DISTINCT clause_id（**仅取 queue review 的**），再对这批 id 发**单条批量条件 UPDATE**（`id IN (…) AND EXISTS(queue review)`），rowcount 即实际写列数；随后一条批量 UPDATE 把该批 queue `review→done`。逐条循环 UPDATE 是明确不采用的反模式。
2. **inline 覆盖驳旧词（C9）**：`inline_edit` 收到 `new_label` 且 ≠ 原 ai_label 时，把该 `(clause_id, dimension)` 残留 pending 词置 `rejected`（并走 `deactivate_fragment` 停用碎片）。**落地顺序与门控**：先经 `_confirm_clause` 写新列，**仅当写列成功（`written=True`，即 queue 仍 review）且 `val != (orig or "")`** 时再驳残留；**ai_label 为 NULL 视为「与任何非空新标签不同」**（否则该状态被静默跳过 → Tab2 之后旧词面回填覆写人工新标签）。写列 no-op 则不驳残留。幂等。
3. **Tab1 确认不碰词 approved（C1）**：写列路径与 `approve_rule` 解耦——规则侧背书只发生在 Tab2 批准、黑名单 approve、或既有 auto 沉淀（已背书词）。**审阅/确认路径的唯一写列出口**：`rule_pending._confirm_clause(conn, clause_id, dimension, label) -> bool`（内置维度白名单，非法维度抛 `ValueError`）。Tab1 `decide`、Tab1 `inline-edit`、低置信 `process_feedback`（`/review/{qid}/confirm` 路径）**全部委托它**，不自带写列副本——三处守卫强度因此天然一致。（该「唯一」限定在审阅/确认路径内；dim4/5/6 分类列另有两处**非审阅**写入方：`batch_queue` 的 auto 免审分支与规范页的人工改维度，二者均不走本守卫，属 C10 设计内的既定点。）
4. **主表终态排除（C5）**：`pending_clause_groups` 增加 NOT EXISTS（queue 该 clause+dim 状态 ∈ done/auto_adopted/rejected）或 join queue 过滤 review——已定案条文不再因残留词出现在主表。

## 五、Tab1 交互（主表）

- 候选词 chip：checkbox 默认全勾；文案小字「未勾选的词将进入黑名单，不再被提议/沉淀」。
- 按钮组：✅ 确认（写列+queue done；去勾词 rejected）| ✏️ 编辑（展开：可删 chip=驳、新标签输入=覆盖+驳残留旧词）| 取消。
- 移除：主表「❌ 批量驳回」按钮、`clauseDecide('reject')` 调用与反向小字。
- 全标签已驳条文（词全 rejected、queue 仍 review）：主表显示「词已驳回，请为条文输入新标签」+ 展开编辑（保留原 D1 语义）。
- **端点契约（`POST /review/clause-pending/{clause_id}/decide`）**：body `{dimension: 必填, ids: 未勾选=要驳回的词 id, action:'approve'}`。`ids` 可为空数组 = **纯确认**（只写列 + queue done，不动任何词）。400 条件：`action != 'approve'`（含已废弃的 `'reject'`）、`ids` 非数组、`ids` 元素非 int、`dimension` 缺失/非字符串/非白名单、`ids` 中有 id 不属于该条文该维 pending 作用域、该条该维无 review 队列项、队列项 `ai_label` 为空。其余 pending 词保持 pending 流入 Tab2。
- **D1 行确认按钮前端守卫**：D1 组（词已全驳）的 `candidates` **恒为空**，该行不渲染任何 checkbox。`clauseConfirm` 入口先判「候选区有无 `.cand-check`」——无则 `alert('该条无候选词，请用编辑输入新标签')` 并 `return`，**不发请求**。理由是空 `ids` 会被后端按「纯确认」写 `ai_label` + queue done，抹掉人工「请为条文输入新标签」的待办，而确认框文案（未勾选进黑名单）在该行并不成立。守判决**不得复用** `:not(:checked)` 选择器——那会连带误伤「有候选词但全勾」的合法纯确认路径。
- **低置信兜底块不变**：仍 include `review_list.html`，仅去掉其中「候选词勾选背书成规则」的 UI 与端点 patterns 参数（confirm 变纯打标）。

## 六、代码落点（收窄版）

| 现状 | 本次改动 |
| --- | --- |
| `apply_ai_results` auto 分支（已背书词→auto_adopted） | 不动（C10 已满足） |
| `rule_pending.pending_clause_groups` 无条件取 pending 词条文 | 加终态排除：NOT EXISTS queue ∈ done/auto_adopted/rejected（C5） |
| `review_clause_decide` approve = 词 approved + backfill + approve_rule | 改：仅写该条列 + queue done；勾选词留 pending；去勾词 rejected（deactivate_fragment） |
| `review_clause_decide` reject 分支 | 删除（C4）；端点可保留但 reject 动作走 400 或弃用 |
| `review_clause_inline_edit` 保留 chip = approve | 改：保留 chip 仅留 pending（不 approve）；new_label ≠ ai_label 时驳残留 pending 词（C9） |
| `review_clause_panel.html` checkbox approve 语义 + 批量驳回按钮 | checkbox 改「去勾=黑名单」、删驳回按钮、词 chip 交互调整 |
| `review_list.html` 兜底 confirm 勾选词背书 | 去词背书：confirm 纯打标写列；patterns 参数停用（C1 延伸） |
| `feedback.process_feedback` patterns 背书分支 | 停用或简化为纯写列（词面沉淀仅 Tab2）；`_fetch_review_items` 不再附带 extract 勾选词。**落地**：`patterns`/`source_conf` 仅保留为签名兼容参数并忽略，函数体委托 `rule_pending._confirm_clause`（不再自带写列副本） |
| `rule_pending._backfill_by_status` 无条件写列 | 加 review 守卫（C8）；落地为单条批量条件 UPDATE + rowcount（C14） |
| 低置信旧端点 /review/list、/{qid}/confirm|reject、batch-* | 保留不删（只语义：confirm 纯打标、不碰词面） |

## 七、错误处理 / 并发 / 幂等

| 场景 | 行为 |
| --- | --- |
| Tab1 主表确认时 queue 已 done / 条文已删 | 写列 no-op（`_confirm_clause` 的 EXISTS(queue review) 守卫，rowcount=0 → 返回 False）；端点仍返回 200 空响应（幂等友好，不把重复点击报成失败）。前端据 `resp.ok` 自行重拉面板（`clauseConfirm` 里的 `refreshReviewPanels()`）；端点同时返回 `HX-Trigger` 以兼容 htmx 发起的调用方——**注意该响应头对本路径无效**（htmx 只处理它自己发起的请求的 `HX-Trigger`），故删掉 `refreshReviewPanels()` 会让重复点击后的刷新静默失效 |
| Tab1 确认 ids 为空 | 纯确认：只写列 + queue done，不 set 任何词状态（合法路径，非错误） |
| Tab1 确认命中 D1 行（无候选词） | 前端 `clauseConfirm` 守卫拦截（alert + return，不发请求）；后端本身不区分 D1 行，靠 `ai_label` 空则 400 兜底 |
| inline 新标签覆盖时残留词已 approved/rejected | 只处理 `status='pending'` 残留（`clause_pending_ids`），幂等；且仅在写列成功且 `val != (orig or "")` 时触发 |
| Tab2 反联时来源条文已 done / 已改标 | 跳过该条（C8），规则照常沉淀 |
| 并发双开（Tab1 确认 vs Tab2 批准同条） | 两条路径都是「条件 UPDATE + rowcount 命中才置 queue done」；同一写事务内先到者命中并置 done，后到者的 EXISTS(queue review) 已不成立 → rowcount=0 → no-op。`_backfill_by_status` 的单条批量 UPDATE 同样逐行按 EXISTS 判定，故「后到者 no-op」是**行级**而非事务级（同批中部分条文可命中、部分被跳过） |
| 低置信 confirm 不再传 patterns | 无词面副作用；旧调用方（batch_confirm）停传 patterns 即纯打标 |
| 审计 | 状态变更沿用 `log_action`（category=review/classify），动作语义更新 |

## 八、测试影响（TDD，收窄版）

**改断言（Tab1 主表「批准=词 approved+bump」语义变化）**：
- `test_rule_pending.py`：`test_tab1_clause_multi_label_approve_partial`（批准只写列、词不 approved、无规则沉淀）、`test_tab1_inline_remove_label_rejects`、`test_tab1_inline_new_label_writes_col_only`、`test_reject_keeps_clause_in_tab1_marked`、`test_tab1_inline_cancel_noop`、`test_word_approve_then_clause_queue_already_done_idempotent` 等（凡断言「主表批准 → rule_pending approved / rules confirmed」改）。
- `test_rule_feedback_loop.py`：`process_feedback` 去 patterns 背书 → 断言「确认背书 patterns → 生成规则」改「纯打标写列、无规则沉淀」（词面沉淀改由 Tab2 承担）。
- `test_batch_queue.py` / `test_classifier_ai.py`：auto 分支语义不变则应通过，仅核对无意外破坏。

**新增**：
1. Tab1 主表确认 → 只写列 + queue done，词不 approved / 不 bump 规则；
2. Tab1 主表确认时去勾词 → rejected（黑名单）；勾选词保持 pending 流入 Tab2；
3. inline 新标签 ≠ ai_label → 残留 pending 词 rejected、Tab2 不再反嚼覆写；
4. 反联守卫：Tab1 已 done / 已改标条文不被 Tab2 反联覆写；
5. `pending_clause_groups` 排除终态：已 auto_adopted 但有残留 pending 词的条文不再出现在主表；
6. 低置信 confirm 纯打标：传 patterns 也不沉淀词面（若保留 patterns 参数则断言其被忽略）。

**低置信块回归**：`test_lowconf_clause_without_pending_still_in_tab1`、`test_clause_pending_get_includes_queue_fallback`、`test_review_batch`、`test_logs`（batch 日志）应**不破坏**（收窄版保留旧端点）。

## 九、风险 / 开放项

- **主表确认与词去勾的关系**：确认时「勾选词留 pending、去勾词 rejected」需在单端点内同事务完成；误把「去勾」当「不处理」会漏驳 → UI 小字明示「未勾选将进黑名单」。
- **C9 驳回残留词作用域**：仅驳该 `(clause_id, dimension)` 以旧 ai_label 背书的残留 pending 词，不跨条文——用户 inline 改标不误伤词在别的条文里的正常首审。
- **C8 守卫对既有 Tab2 反联的影响**：改标/已 done 条文不再被词面反联覆写——需回归 test_word_approve_then_clause_queue_already_done_idempotent（期望 done 条文不被重写但规则仍沉淀）。
- **auto_adopted 残留词的去向**：排除出主表后，残留 pending 词仍进 Tab2 池（规则沉淀视角），不影响「条文已 auto 定案」事实。
- 全量回归面可控（低置信块不动，破坏集中在主表语义 Task）。

## 十、验收口径

- 删除重导一个规范并跑 AI 分类后：Tab1 主表仅剩「queue 仍 review 且带 pending/已驳词」的条文（已 auto_adopted 残留词条文不重出）；主表批准只写列不建规则（classification_rules 计数不变）；Tab2 批准词面 → 名下 review 条文写列 done；inline 改标后旧词不反嚼。
- 低置信兜底块、黑名单恢复/批准、免审 auto 路径回归通过。
- **红点口径**：`pending_counts()` 的 `clause` 段与 Tab1 三个数据源（`pending_clause_groups` + `rejected_clause_groups` + 低置信兜底）**谓词逐字同口径**，且为纯 COUNT 不物化 group 对象（该端点被前端 30s 轮询，禁止 N+1）。注意兜底来源的**展示**查询带 `LIMIT 50`（`_fetch_review_items`），即红点计数可能大于列表可见条数——该差异是既有的、非本 plan 引入。
- **端点契约**：`/review/clause-pending/{id}/decide` 在 `action != 'approve'` / `ids` 非 int 数组 / `dimension` 缺失或非白名单 / 无 review queue 项 / `ai_label` 空 / ids 越出该条文该维作用域 时一律 400；空 `ids` 是合法纯确认。
- **D1 行**：候选全驳、`candidates` 恒空的行，其「✅ 确认标签」按钮被前端守卫拦下（不发请求），只能走「✏️ 编辑」输新标签。
- **测试**：全量套件绿（Task 7 收尾 724 passed）。渲染类断言无法执行内联 JS 分支，故 `tests/test_review_clause_panel.py` 对 `clauseConfirm` 采取「抠函数源码 + 位置断言（守卫早于 confirm/fetch）」；分支行为另经 Node 执行真函数三例验证（无候选 → 不发请求；全勾 → ids:[] 纯确认；去勾 → ids:[该 id]）。
