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

