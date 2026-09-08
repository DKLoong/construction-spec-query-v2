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

1. **反联守卫（C8）**：`_backfill_by_status` 写列前须确认该条 `queue.status='review'`（或等价：clauses 列未被人工改标）。已 done / 已改标的来源条文跳过写列，仅统计。
2. **inline 覆盖驳旧词（C9）**：`inline_edit` 收到 `new_label` 且 ≠ 原 ai_label 时，把该 `(clause_id, dimension)` 残留 pending 词置 `rejected`（并走 deactivate_fragment 停用碎片），再写新列。幂等。
3. **Tab1 确认不碰词 approved（C1）**：写列路径与 `approve_rule` 解耦——规则侧背书只发生在 Tab2 批准、黑名单 approve、或既有 auto 沉淀（已背书词）。
4. **主表终态排除（C5）**：`pending_clause_groups` 增加 NOT EXISTS（queue 该 clause+dim 状态 ∈ done/auto_adopted/rejected）或 join queue 过滤 review——已定案条文不再因残留词出现在主表。

## 五、Tab1 交互（主表）

- 候选词 chip：checkbox 默认全勾；文案小字「未勾选的词将进入黑名单，不再被提议/沉淀」。
- 按钮组：✅ 确认（写列+queue done；去勾词 rejected）| ✏️ 编辑（展开：可删 chip=驳、新标签输入=覆盖+驳残留旧词）| 取消。
- 移除：主表「❌ 批量驳回」按钮、`clauseDecide('reject')` 调用与反向小字。
- 全标签已驳条文（词全 rejected、queue 仍 review）：主表显示「词已驳回，请为条文输入新标签」+ 展开编辑（保留原 D1 语义）。
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
| `feedback.process_feedback` patterns 背书分支 | 停用或简化为纯写列（词面沉淀仅 Tab2）；`_fetch_review_items` 不再附带 extract 勾选词 |
| `rule_pending._backfill_by_status` 无条件写列 | 加 review 守卫（C8） |
| 低置信旧端点 /review/list、/{qid}/confirm|reject、batch-* | 保留不删（只语义：confirm 纯打标、不碰词面） |

## 七、错误处理 / 并发 / 幂等

| 场景 | 行为 |
| --- | --- |
| Tab1 主表确认时 queue 已 done / 条文已删 | 写列 no-op（WHERE status='review' 守卫），返回成功计数 |
| inline 新标签覆盖时残留词已 approved/rejected | 只处理 pending 残留，幂等 |
| Tab2 反联时来源条文已 done / 已改标 | 跳过该条（C8），规则照常沉淀 |
| 并发双开（Tab1 确认 vs Tab2 批准同条） | 写连接事务内先到者生效，后到者 no-op |
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
