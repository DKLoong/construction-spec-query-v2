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
