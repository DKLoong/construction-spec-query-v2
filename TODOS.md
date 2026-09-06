# TODOS

> Deferred 工作登记。条目来自代码评审（plan-eng-review）中被否决/延后的方向，附触发条件与上下文，避免「记了却不知道为什么记」。

## T1 — 检索索引侧 OOV 词条增强（触发式）

- **What**：若探针测试证实「规范词输入无法召回含俗词的条文」（即条文里俗词未被 jieba 切成独立 token），需在 `build_search_text` 做词条预扩展，或引入 jieba userdict 后重建 `search_text`/FTS 索引。
- **Why**：词库同义词扩展只保证「俗词输入 → 扩出规范词命中规范条文」（查询端可控）；反向依赖索引侧俗词被切出，当前未经验证。
- **Context**：spec `docs/superpowers/specs/2026-09-04-lexicon-module-design.md` §5.5 与 §十二。触发条件 = `test_lexicon_expand`/`test_hybrid_search` 中的探针测试失败。届时涉及存量 `search_text` 重建（参照 `scripts/migrate_fts.py`）+ jieba 全局词典热更新。
- **Blocked by**：词库检索扩展落地（spec 收尾验收）之后，仅当探针失败才启动。

## T2 — confusable「单用歧义」检测（积累后评估）

- **What**：把易混淆命中从「原问句同现 A、B」扩展到「用户只问 A 时提示其常与 B 混淆」。
- **Why**：当前语义防的是「并列两概念求区分」；单用歧义触发面更广但需为词库词标注默认义，误报风险高（用户问规范词也被弹「你是不是想问别的」），词库零/低种子下上线即空转。
- **Context**：spec §5.4 注释保留了该语义取舍与理由。待 confusable 真实词条积累后评估是否值得。
- **Blocked by**：词库模块 + confusable 种子投入使用。

## T3 — 术语/分类知识库（另立立项，非 deferred）

- **What**：独立子系统：`术语 ↔ dim4/5/6 维度标签` 权威映射词典，服务规则引擎与 AI 分类，兼作未来 jieba userdict 词源。
- **Why**：规则引擎/AI 分类缺「更准的匹配词表」——术语→维度标签映射可自动补 `classification_rules` pattern、给 AI classify prompt 权威参照减少幻觉标签；并天然收纳**无同义/别名的独立纯术语**（现在三类词库无处放这类词）。
- **Context**：2026-09-04 会话决策：与词库检索/问答增强是**两套数据与消费链**（数据模型 term↔标签多对多、消费方是 classification_rules / AI classify，与 lexicon 异源），故不并入词库 spec，另开 brainstorm → spec → 计划。词库的 canonical 可作其词源来源，注意复用而非重建。
- **入口候选**：先调研现有 rule feedback loop（`extract_keywords` 从 QA/条文提词→规则）与 AI classify prompt 的标签清单，据此设计 term 与维度标签的粒度。
- **Blocked by**：无硬依赖；建议在词库子系统实施完成后排期（避免两条大链并行）。
- **Status（2026-09-06）**：❌ **已否决并回滚**。termdict 实现（11 commits `2b24aaf..b95447a`）已 `git revert`（`86ca38c`）。否决原因：碎片污染应在**源头**（auto_adopt 沉淀动作加首审门槛 + 驳回进黑名单）治理，而非在结果端建权威标签词典；且 termdict 同义词与 [[2026-09-04-lexicon-subsystem|lexicon]] 重合易乱套。spec/plan 文档保留供参考（含上述判定），但不再实施。

## T4 — termdict 收口遗留 minors（deferred，2026-09-05 整仓评审后登记）

> ⚠️ **2026-09-06 作废**：termdict 已整体回滚（见 T3 Status），本条遗留随之失效，不再处理。

> 来源：termdict SDD 各 Task 评审 defer 项汇总。全部「可 defer」，无 merge 阻塞；择机一次 fix wave 处理。

- **What / 触发清单**：
  1. 测试脆弱：`tests/test_termdict_gates.py` 闸门①「有效 label 落列」用例规则得分恰落 dim4 AI 阈值边界（config `ADAPTIVE_THRESHOLDS["dim4"]=0.6`；上调即翻车）——触发 = 改该阈值时；修 = 测试规则 threshold 调低或条文得分拉高离开边界。
  2. 编辑保存双触发：`termdict_edit_row.html` 保存走「服务端 HX-Trigger + form hx-on::after-request 手动 dispatch」双路 → 每次保存两次 `GET /termdict/list`（幂等冗余）。
  3. 测试覆盖缺口：routes「编辑改词面跨 label 拒绝」无用例、400 未断言 DB 未落行、无 HX-Trigger 头断言；`collect_label_candidates`「词典不可用→现值兜底」未直达（store 层已覆盖冲突态）；`test_termdict_migrate.py` 未断言 `source=='migrate'`。
  4. 小代码债：`store.py` `_is_trusted(rows)` 死 helper、`_row_from` canonical 未 strip（写入侧已 strip 无脏数据）、宽捕获可 `logger.exception`；`migrate_term_dict.py` `skipped_conflict` 死计数（单线程恒 0）+ except 只 print 丢堆栈；`upsert_term_label` 无直接单测（Task6/9 间接覆盖）；`termdict_routes.SOURCE_TEXT` 死值 `seed`（无产出路径）；`valid_labels()` 无生产消费方（Task7 改用 load_active_entries）。
  5. `tests/test_logs.py`「无 queue 行→review」收紧点无直接回归用例（test_logs 已适配，可补一条队列外 result 的显式断言）。
- **Why**：评审时多数属过设计防御/死码/文案，逐项修性价比低，故 defer；2/3 项影响维护者心智与回归健壮性。
- **Context**：spec `2026-09-05-termdict-design.md` §五/§十二；各 Task review 文件已随 SDD workspace 清理，本条目为唯一留存出处。

