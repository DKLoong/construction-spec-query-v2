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

## T5 — jieba userdict 术语库（原 T3 用户初衷，另立项）

- **What**：建一份工程术语表灌入 jieba（`jieba.add_word`/词典），使 `extract_keywords`（提词→规则 pattern）、规则 keyword 匹配、`build_search_text`/FTS 索引三处对术语一致切分（如「钢筋机械连接」「屈服强度」不被切碎）。必要时随索引 OOV（[[lexicon-subsystem]] T1）联动。
- **Why**：2026-09-05 用户澄清 T3 的真实初衷是「术语→切词更准」，而非 termdict 标签收口（已回滚）。是 rule-pending 之外的正交能力。
- **Context**：词库 lexicon canonical/aliases、日后 rule_pending 未决词可作为术语词源候选；先做「词源→jieba 词典」最小闭环，再评估对 `extract_keywords`/检索的收益。
- **Blocked by**：无硬依赖；建议 rule-pending 落地并跑通后再开 brainstorm。
- **Status（2026-09-06）**：由 /plan-eng-review 扫出并登记。
- **Status（2026-09-19）**：⏸ **降级搁置**（探针实测后用户裁定）。三条依据：
  1. **检索收益被下游吸收**：离线探针（`scripts/probe_jieba_terms.py`）只测 FTS 单通道，测得「子词查询丢失 17.1%、精度 +60 条次」；但真实链路是 `FTS + 向量 → RRF → CE 精排`——噪声被精排压下、召回被向量通道补上。**教训：FTS 单通道差值不能当端到端收益报。**
  2. **词源近乎无效**：T5 假设「lexicon canonical 可作术语词源」不成立。366/658（56%）canonical 确被切碎（收益面真实），但本语料 18 个真实领域术语（`接头百分率`/`屈服强度`/`残余变形`/`最大力总伸长率`…）lexicon **只覆盖 1 个**。
  3. **提词侧也不划算**：碎片黑名单三桶分解（见下 T6）显示 T5 只能碰 B 桶 37%（且是上限）。
- **技术结论（若日后重启可直接用）**：①**索引侧与查询侧必须同词典，缺一即全失配**（baseline 索引 + 词典查询命中 **0/54**），不能只改一边或灰度；②**双写**（`treatment token ∪ baseline token`）可两头都要：子词查询损失 0、术语查询保持精确，代价索引 **2.00x**；只加词典不双写则净负（丢失 376/2193）；③全项目**无 jieba 初始化点**（`tokenize.py` 用 `jieba.cut`、`feedback.py` 用 `jieba.lcut`，共享模块级默认 Tokenizer，故一次 `add_word` 两边生效），引入词典须新增唯一初始化点。
- **附带**：本探针同时回答本条 §T1 的触发条件——「术语被默认词典切碎」是**普遍现象**（56% canonical），T1 与 T5 是同一枚硬币两面。

## T6 — AI 分类「护栏 auto + 抽查」平衡（稳态 pending 观测后触发，非 deferred）

- **What**：在 rule-pending「首审+黑名单」之上，为「大部分 auto、少量人工」的半自动原意加一道**多因子护栏**：满足全部护栏的新 (pattern, label) 组合直接 auto 沉淀（进低比例抽查），否则维持现状词面首审。护栏：
  - **G1** label ∈ 该维已背书/锁定的已知合法值集（非凭空新标签）；
  - **G2** pattern 通过质量过滤（非 HTML/动作词/停用词/过短、不在黑名单）；**G2 是核心，须先离线回测**：用历史碎片（style/td/检验/试验…）测「拦截率（拦下多少碎片）vs 误伤（好词被错拦）」，达标才敢开 auto 通道；
  - **G3** 高置信 +（可选）词面在多条文支持度。
  - 三关全过 → auto 沉淀 + **抽查率参数**（registry 可调，低比例人工复核保纠错）；任一不关 → 现状词面首审。
- **Why**：用户体感人工量大增，拆因后主要为**一次性历史积压**（398 pending + 存量条文重跑提词），稳态量应远小。平衡 = 让 auto 部分重新可信（当年 0.7 阈值被全 ≥0.9 高置信绕过 → 置信度单因子不可靠，[[ai-classify-confidence-calibration]]），故必须多因子护栏而非放开阈值。
- **触发 / 前提**：先小批量导入跑几天，**观测稳态 pending 新增量**（每天新「首见组合」数）再决定是否值得做；若稳态已足够小（首审≈可接受），本项可推迟。做前先离线验证 G2 拦截率。
- **Context**：2026-09-06 用户裁定清理 dev 至「locked 种子基准」（dim4/5/6 active=21/23/23，删除 398 碎片规则+398 pending/黑名单归零）；钢筋严格只归 dim6。候选现从锁定种子取，新材料走首审。对话亦讨论「钢筋同跨 dim4/5/6 乱标」成因 = 候选被碎片污染（跨维共词）而非纯 prompt/LLM 不足；候选收口后 prompt 判别增强（每维判定标准+正反例+冲突词提示）为候选层之外的可选第二层。
- **Blocked by**：稳态 pending 观测结果；rule-pending 机制在 dev 实际跑一段时间。
- **Status（2026-09-06）**：思路登记。不做；待用户小批量观测稳态后决定是否 brainstorm→spec→plan。
- **Status（2026-09-19）**：仍暂缓，但**前置条件有实质进展**。
  - ❌ **「稳态 pending 观测」依然不成立**：dev 库 `rule_pending` 529 行全部落在 `2026-09-06 18:35:36–18:35:49`（**14 秒内**），09-06 之后 13 天**零新增**；`clauses` 73 条与 `classification_queue` 178 行同样全在 09-06。所谓「13 天观察期」观察的是**人工审核进度**，不是新导入材料的首见组合增量。故「人工量大是历史积压、稳态很小」既未证实也未证伪。
  - ✅ **G2 离线回测的标注集已具备**：389 行人工驳回 = 现成标注集。按根因三桶分解（`scripts/probe_markup_residue.py` 同源分析）：
    | 桶 | 根因 | pattern / 行 | 占比 | 谁能治 |
    | --- | --- | --- | --- | --- |
    | A 标记残留 | 导入解析层（PaddleOCR-VL 输出） | 13 / 90 | 23% | ✅ 已于 2026-09-19 修复 |
    | B 术语碎片 | 分词粒度 | 24 / 144 | 37% | jieba 词典（T5，已降级） |
    | **C 通用真词/自指** | **词的特异性** | 36 / 155 | **40%** | **G2 质量过滤（本条）** |
  - **关键判断**：C 桶（最大）是**完全合法的词**（`试件`x32、`加工`、`本规程`、`丝头`、`抽检`、`长度`、`合格`），被驳回不是因为切碎了，而是**太泛、做 pattern 没区分度**——术语词典一个字都救不了，这正是 G2 的靶心。且这些 pattern 多为通用词（`style`/`试件`/`本规程`），**比术语词典更不容易过拟合到单份文档**。
  - **诚实边界**：桶归属含判断（`检验` 算不算"碎片"可争议），**比例是指示性而非精确的**；仍是单份规范、73 个 pattern。


