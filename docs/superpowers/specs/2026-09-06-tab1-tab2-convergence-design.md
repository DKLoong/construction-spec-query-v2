# Tab1/Tab2 打标-沉淀解耦（词面收敛）设计

日期：2026-09-06
状态：待评审
关联系统：施工规范智能查询平台 V2（FastAPI + SQLite FTS5 + LanceDB）
上游：`2026-09-06-rule-pending-review-design.md`（AI 沉淀规则「首审+黑名单」）落地后的人工量治理；对齐用户 Q3/Q4 观察（删除重导 JGJ107 → 71 条 178 项 → 数页待审）。

## 一、背景与问题（实证）

**现状数据流**（rule-pending 裁决，已核实）：
- `apply_ai_results`：AI 对每个「条文×维度」给 1 个 label 后，`extract_keywords(content, top_n=3)` 把 **top3 关键词各建一条 pending** `(clause_id, dimension, pattern=词, label)`（`batch_queue.py:108`）。
- dev 实证：71 条文 → 178 个「条文×维度」pending 组，**173 组恰好 3 个候选、178/178 组内部 label 全相同**（同 label、不同词面）。529 行 pending。
- 词面批准（Tab1/Tab2）`approve_rule` 会把该词面写成规则，供未来导入**确定性自动分类**（`classify_clause`，`import_routes.py:419`）。

**问题**：
1. **「条文打标」与「词面沉淀」两件事被绑在同一批 checkbox 上**。Tab1 批准一个条文 = 展开 3 个词 `resolve_keys → approve_rule`，为同一 label **同时建 3 条规则**（含 `规范/混凝土` 碎片）→ 碎片污染复发路径。
2. **Tab1 视觉噪音**：同一条文同 label 的 3 个词 chip 长得一样（用户 Q3 问「为何每条 3 个源」）——chip 是 3 个待背书规则词，不是 3 个来源。
3. **Tab1 主源 `pending_clause_groups`（按「有 pending 词」聚合）**会把「已 auto_adopted 但有残留词」的条文也带进 Tab1（实测 5 组），与「条文待审」语义不符。
4. 人工量大（用户 Q4）：529 全量 pending，但唯一需人工背书的组合仅 201 个（distinct dim+pattern+label）。词面聚合（Tab2）是批量杠杆，应为主链。

**本 spec 只做语义/交互收敛（①）**。③ prompt+候选隔离、④ G1/G2/G3 护栏回测另立 spec；rule_pending 表结构不动，接缝留好。

## 二、核心决策（与用户逐点确认）

| # | 决策 |
| --- | --- |
| C1 | **打标与沉淀解耦**：Tab1 批准条文 = 只写分类列 + queue `review→done`，**不 set 词状态、不 approve_rule**。词面沉淀只经 Tab2 一个出口。 |
| C2 | **词面池来源 = 维持现状「先提词」主链**（AI 提案词全进池，Tab2 按词聚合）。不做「打标后提词」——会丢掉「批一个词面=反联批量打标」的聚合杠杆。 |
| C3 | **Tab1 词 chip checkbox 保留，语义 = 去勾即驳回（黑名单）**：勾选（默认全勾）= 该词保留为 Tab2 候选（pending 不动）；去勾/× = 该词 `rejected`。**不标 approved**。 |
| C4 | **删 Tab1「批量驳回」按钮**：标签不认 → ✏️ 编辑输新标签覆盖；词不认 → 去勾/×（黑名单）。无独立驳回入口。 |
| C5 | **Tab1 数据源三源合一** = 全部 `queue status='review'` 条文（含低置信无词、词全驳待重标）；auto_adopted 条文自然不再出现。 |
| C6 | **Tab1 = 全部待打标条文总览 + Tab2 词面聚合未覆盖的补充**，与 Tab2 共享同一批 queue/pending 对象，任一入口完成即推进。 |
| C7 | **词 chip「同 label 3 词」UI 保留**（用户判定有助于黑名单精修），不改展示形态。 |
| C8 | **反联守卫**：`backfill_and_close` 只写 `queue status='review'` 的条文，Tab1 已定案/改标条文绝不被词面反嚼覆写。 |
| C9 | **inline 新标签 ≠ 原 ai_label → 驳回该条残留 pending 词**（一致性规则，防 Tab2 之后批准旧词面回头覆写人工新标签）。 |
| C10 | **免审链保留**：词面已背书（approved 或规则 confirmed>0 且 active）→ AI 再提直接 `auto_adopted`（写列+dump），不进 Tab1。 |

### 非目标（本期不做）
- ③ prompt 判别增强 / 候选维度隔离（另 spec）。
- ④ G1/G2/G3 词面质量护栏 + 抽查率回测（另 spec）。
- Tab2 词面池结构、黑名单管理语义、规则引擎本身（不动）。
- rule_pending / classification_queue 表结构变更（语义改动即可满足）。

## 三、数据流（四步闭环）

```text
AI 分类批 apply_ai_results
  ├─ 词已背书(approved 或规则 confirmed>0 且 is_active) → queue auto_adopted：
  │    直接写列 + bump(命中)，不进 Tab1（C10）
  └─ 词首见(none) → 各词 insert_pending + queue 留 review（进 Tab1 总览 + Tab2 池）

Tab1（queue review 总览，C5）
  ├─ ✅ 确认标签 = 写 ai_label 列 + queue done；勾选词留 pending(→Tab2)、去勾词 rejected（C1/C3）
  ├─ ✏️ 编辑/新标签 = 写新 label + queue done + 驳回残留 pending 词（C9）
  ├─ 词 chip × = 单词 rejected（黑名单）（C3）
  └─ （已删除：批量驳回按钮 C4）

Tab2（pending_groups 按 dim+pattern 聚合，沉淀主链）
  ├─ 批准词面 = approve_rule(规则学习，唯一沉淀出口) + backfill 反联 review 条文写列 done（C8）
  └─ 驳回词面 = rejected + deactivate_fragment(停用 confirmed=0 碎片)，不写列

黑名单管理（不变）：restore(rejected→pending) / approve(rejected→approved + 反联)
```

## 四、一致性守卫（核心新增）

1. **反联守卫（C8）**：`_backfill_by_status` 写列前须确认该条 `queue.status='review'`（或等价：clauses 列未被人工改标）。已 done / 已改标的来源条文跳过写列，仅统计。
2. **inline 覆盖驳旧词（C9）**：`inline_edit` 收到 `new_label` 且 ≠ 原 ai_label 时，把该 `(clause_id, dimension)` 残留 pending 词置 `rejected`（并走 deactivate_fragment 停用碎片），再写新列。幂等。
3. **Tab1 确认不碰词 approved（C1）**：写列路径与 `approve_rule` 解耦——规则侧背书只发生在 Tab2 批准、黑名单 approve、或既有 auto 沉淀（已背书词）。

## 五、Tab1 交互与模板（review_clause_panel）

- 候选词 chip：checkbox 默认全勾；文案小字「未勾选的词将进入黑名单，不再被提议/沉淀」。
- 按钮组：✅ 确认（写列+queue done；去勾词 rejected）| ✏️ 编辑（展开：可删 chip=驳、新标签输入=覆盖+驳残留旧词）| 取消。
- 移除：❌ 批量驳回按钮；`clauseDecide('reject')` 调用与反向小字。
- 全标签已驳条文（词全 rejected、queue 仍 review）：主表显示「词已驳回，请为条文输入新标签」+ 展开编辑，不并入低置信兜底（保留原 D1 语义）。
- 低置信无词条文：并入同一总览（queue review），无词 chip，仅 确认/编辑。

## 六、代码落点

| 现状 | 本次改动 |
| --- | --- |
| `pending_clause_groups` / `rejected_clause_groups` + `_fetch_review_items` 三套 Tab1 源 | 合一：`queue review` 为主源，关联该条 pending/rejected 词；低置信无词与全驳待重标并入渲染分支 |
| `review_clause_decide` approve = 词 approved + backfill + approve_rule | 改：仅写列 + queue done；未勾选词 rejected（deactivate_fragment）；勾选词不动 |
| `review_clause_inline_edit` 保留 chip = approve | 改：保留 chip 仅留 pending（不 approve）；new_label 覆盖后驳残留旧词（C9） |
| `feedback.process_feedback`（低置信确认，写列+勾选词背书） | 与 Tab1 统一动作对齐（写列 + queue done；不隐式 approve 词）——并入合一源后由同一端点处理 |
| `_backfill_by_status` 无条件写列 | 加 review 守卫（C8） |
| `review_clause_panel.html` checkbox approve 语义 + 批量驳回按钮 | checkbox 改「去勾=黑名单」、删驳回按钮、词 chip 交互调整 |
| 现有词 chip 形态 | 保留（C7） |

## 七、错误处理 / 并发 / 幂等

| 场景 | 行为 |
| --- | --- |
| Tab1 确认时 queue 已 done / 条文已删 | 写列 no-op（WHERE status='review' 守卫），返回成功计数 |
| inline 新标签覆盖时残留词已 approved/rejected | 只处理 pending 残留，幂等 |
| Tab2 反联时来源条文已 done / 已改标 | 跳过该条（C8），规则照常沉淀 |
| 并发双开（Tab1 确认 vs Tab2 批准同条） | 写连接事务内先到者生效，后到者 no-op |
| 审计 | 状态变更沿用 `log_action`（category=review/classify），动作语义更新 |

## 八、测试影响（TDD）

**改断言（原「Tab1 approve = 词 approved + bump」语义）**：
- `test_rule_pending.py`：`test_tab1_clause_multi_label_approve_partial` / `test_tab1_inline_remove_label_rejects` / `test_tab1_inline_new_label_writes_col_only` / `test_decide_*`、`test_reject_keeps_clause_in_tab1_marked` 等；
- `test_review_batch.py`、`test_batch_queue.py`、`test_classifier_ai.py`（auto/confirm 语义变化）。

**新增**：
1. Tab1 确认 → 只写列 + queue done，词不 approved / 不 bump 规则；
2. Tab1 确认时去勾词 → rejected（黑名单）；勾选词保持 pending 流入 Tab2；
3. inline 新标签 ≠ ai_label → 残留 pending 词 rejected、Tab2 不再反嚼覆写；
4. 反联守卫：Tab1 已 done / 已改标条文不被 Tab2 反联覆写；
5. 已背书词 → auto_adopted 不进 Tab1；
6. Tab1 源合一后 auto_adopted 残留词条文不再出现在 Tab1；
7. 删批量驳回后无 reject 端点为残留（或保留端点但仅作文档化废弃）。

**UI 冒烟**：Tab1 词 chip 去勾/确认/编辑全流程；Tab2 / 黑名单回归；红点计数（pending-count）语义随源合一校验。

## 九、风险 / 开放项

- **写列路径收敛面大**：Tab1 确认 / inline / 低置信确认 / auto 四路写列，须统一走单一 helper（防未来再出现「某路径偷偷 approve 词」）。规划时收敛为 `queue 状态机 + clauses 列更新` 一个函数。
- **Tab1 源切换后 pending-count 计数口径**需与合一后 Tab1 显示项对齐（红点/徽标才不失真）。
- **C9 驳回残留词可能误伤**：用户 inline 改标只是「AI 建议 label 不准」，但词本身将来可能出现在别的正确条文里——驳回仅限「该条残留 pending 词（以旧 ai_label 背书）」，不跨条文；词面将来在其它条文再提仍走正常首审。spec 明确作用域。
- 全量回归面：Tab1 语义双变，Task 化分步验证（693 → 预期波动，逐批适配）。

## 十、验收口径

- 删除重导一个规范并跑 AI 分类后：Tab1 仅剩待打标条文（已 auto_adopted 不重出）；Tab1 批准只写列不建规则（规则表计数不变）；Tab2 批准词面 → 名下 review 条文写列 done；inline 改标后旧词不反嚼。
- 词面校核（Tab2）为唯一规则沉淀人工出口；黑名单恢复/批准语义回归通过。
