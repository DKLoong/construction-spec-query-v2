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

## T7 — 规则阈值不一致 + 计分公式对低频词的过度惩罚（2026-09-20 实测登记）

- **What**：两件事须一起看：
  1. **阈值不一致**：`scripts/seed_rules.py` 里 dim2/dim3/dim4 种子声明 threshold=**0.25**，但库内 **0 条**规则是 0.25，实际全是 0.5(32)/0.6(51)/0.7(23)。且 seed 脚本幂等（同 `(dimension, sub_field, pattern)` 已存在即 skip）——**99 条规则 created_at 全为 2026-06-29，说明该脚本从未真正插入过任何一行，一直在空转**。（另 7 条 created_at=2026-09-19，正是 AI 沉淀规则。）
  2. **计分公式**：`keyword 得分 = min(1.0, 0.3 + 词频×0.15) × (1 + 0.1×priority)`，且同维取**最佳单条**（多规则**不累加**）。代入 0.5 阈值 → **词在条文中出现 1 次 = 得分 0.33~0.36 < 0.5，既不写分类列也不免 AI；必须 ≥2 次才有效**。
- **Why**：这使「补关键词」的收益被腰斩——词表补得再全，只要该词在目标条文里只出现 1 次，收益为 0。实测反事实（改成「1 次命中即 0.6」）达标条数：dim4 **4→12**、dim5 **36→56**、dim6 **57→71**、dim2 22→36。**改公式是免费的、全局的、立即对全部现有规则生效；补词是逐词的、要赌目标文档里有那个词。**
- **Context**：2026-09-20 会话为回答「现在人工大批量补 dim2~dim4 标签+关键词值不值得做」而实测（97 条激活规则 × 73 条条文，直接调 `classify_clause`）。附带发现：`classify_clause` 写 `best_labels` 的门槛是**规则自身 threshold**，而 `should_use_ai` 用 `ADAPTIVE_THRESHOLDS`——两者相差约一倍，是「打上标」与「免 AI」两件事被混谈的根源。
- **风险**：放松阈值/改公式会让更多词单次命中即打标，**误标风险随之上升**（`试件`/`本规程`/`合格` 这类 C 桶通用词单次命中就会打标——见 T6）。须与 T6 的 G2 质量过滤一起评估，不可单独放开。
- **Blocked by**：无硬依赖；但**建议在 T8（补词）之前处理**，否则补词收益无法兑现。需用户裁定严格度（放宽到多少 / 是否改公式 / 是否保留「≥2 次」）。
- **Status（2026-09-20）**：已登记，未实施。

## T8 — dim2~dim4 词表按导入计划分专业补充（触发式，不现在做）

- **What**：人工补充 dim4（条文级）专业标签词；dim2/dim3（规范级）最后考虑。
- **Why（为什么不现在做，实测三条）**：
  1. **瓶颈在文档侧不在词表侧**：dim4 实测命中仅 **4/73**——21 条 dim4 激活规则里，19 个专业词（给排水/暖通/隧道…）在这份 JGJ107《钢筋机械连接技术规程》里**一次都没出现**。单专业文档，补再多词也改变不了它不涉及那些专业。
  2. **低频词白补**：见 T7，词出现 1 次 = 收益 0。
  3. **dim2/dim3 是规范级判定，一文档一次**：补 dim2/3 词表收益 = 每份新规范省 1 次人工勾选（原设计即「低于阈值 → 导入界面人工勾选，无需 AI」）。量级太小。
  - 另注：dim3 实测 **0/73** 命中是**正常的**——`住宅/学校/医院/新建/扩建/大型` 本就不出现在条文正文，它们该在**规范名称**上匹配（现按条文 content 匹配，属维度挂载位置问题，与 T7 的口径混谈同源）。
- **触发条件**：用户已确定「下一批要导入的是哪些专业的规范」→ **只补那几个专业的词**，且优先挑会在目标文档中**高频出现（≥2 次）**的词。**不要现在穷举**（穷举得到的是「备而未用」——不违背原意，但不产生收益）。
- **Context**：2026-09-20 会话，用户提问「现在对 dim2~dim4 人工大批量补标签及关键词，对后续导入是否有可观收益」。用户同时确认「规则命中为零时分类树不显示，这种备而未用状态与最初构想吻合」。
- **Blocked by**：用户明确下一批导入范围；以及 T7（阈值/公式）。

## T9 — 规则 label 解耦与规则页可用性：7 项全部完成（2026-09-20）

> 2026-09-20 会话判定「规则页与设想偏差」的成因后，同轮修掉四项；此处登记**未做**的余项与**行为变更提示**。

- **已修（4 项，见对应 commit）**：
  1. `rule_sink.bump_rule` 的 label 回填不再改写 `locked=1` 的人工种子规则——实证污染是 dim6 种子「混凝土」被回填成 `label='钢筋'`（JGJ107 单文档局部语料污染全局词表）。
  2. `collect_label_candidates` 改取 `COALESCE(label, pattern)`：解耦后 label 有值的规则其 pattern 只是特征词（套筒/保护层/丝头），原实现把特征词当候选标签喂给 AI。
  3. `build_classify_prompt` 候选标签段改为「必须从其中选择，不得使用列表之外的标签」（原为「若确实不匹配可新建更贴切标签」——该放行句是标签漂移的授权点）。**行为变更**：候选非空时 AI 不再能新建标签，词表覆盖不足时会被迫就范，须靠补词表解决而非放开约束。
  4. 规则页支持 `label`（列表列 + 新建/编辑表单 + 路由写库）：原先整条 UI 链路都不认识 label，人工只能建「词即标签」旧语义规则。

- **✅ 已完成（第 5 项，2026-09-20 第二轮）**：**「按标签分组」视图**（用户选定形态 B：标签分组折叠）。
  - `GET /rules/grouped`：分组键 = `(dimension, label)`，label 为空归入该维「词即标签」组；未设标签组按维度各自成组（不混成一个全局组）。单次查询取全量后内存分组，避免块内查库。排序：有标签组在前（命中降序），未设标签组押后。默认折叠态：有标签组展开、未设标签组折叠。
  - 规则页加「📋 按规则 | 🏷 按标签」tablist 切换（对齐 `/review` 约定）。`#rules-table` 去掉 htmx 加载、统一走 JS `loadRules()`——原 htmx 与 JS 两条加载路径并存，htmx 那条绕过视图模式恒取平铺列表。
  - 分组视图行内操作（启停/锁定/删除）走 JS 处理器：调接口后整体重载 + 派发质量报表刷新（分组视图没有对应的 `<tr>`，注入扁平行会串味）。`updateRule` 在分组视图下亦改为整体重载（改标签会换组）。组头「+ 添加关键词」预填该组维度与标签后打开既有新建弹窗。
- **✅ 附带修掉（第 6 项）**：**移除 `exact` 匹配模式**。实测它要求 pattern 逐字等于「归一化正文 + 过滤后父路径」拼接串，等于为每条条文抄一遍全文，实战永不命中（有父路径时连抄原文都不成立）；库内 113/113 规则均为 keyword，从未被使用。留着只让人得到一条静默死规则。后端分支保留并注明原因（避免对存量行静默改行为）。
- **✅ 附带修掉（第 7 项）**：**关键词写法提示**（实测得出的两个坑）：① 条文先经词库归一化（俗称→规范词）而 pattern 不归一化，**写俗称将永不命中**；② 按子串计数不判词边界，`钢筋` 会命中 `预应力钢筋`，**过泛短词易虚高**；③ 同一标签配多个关键词＝多条规则共享同一 label，**逗号分隔不生效**（填在关键词框规则彻底废置；填在标签框语义是"一关键词→多标签"，与所需相反）。提示以行内小字 + 聚焦浮层两处呈现。
- **Blocked by**：无。
- **Status（2026-09-20）**：7 项全部完成。

## T10 — pico.custom.css 的颜色变量实际未生效（仅颜色，非颜色变量正常）

- **What**：`static/pico.custom.css` 的 `:root` 里定义的**颜色**变量全部被压掉，运行时取到的是 Pico 默认值。若不做 UI 走查不会发现（非颜色变量正常生效，掩盖了问题）。
- **实测证据**（浏览器 `getComputedStyle(document.documentElement)`，页面 8001 隔离实例 `/rules`）：

  | 变量 | 自定义表声明 | 运行时实际值 | 结果 |
  | --- | --- | --- | --- |
  | `--pico-primary-background` | `#8a9e8b` 鼠尾草绿 | **`#0172ad` 默认蓝** | ❌ 被压掉 |
  | `--pico-background-color` | `#f4f0ea` 暖奶油白 | **`#fff`** | ❌ 被压掉 |
  | `--pico-secondary-hover` | `#8b7d71` | **`#48536b`** | ❌ 被压掉 |
  | `--pico-contrast-background` | `#4a4454` | **`#181c25`** | ❌ 被压掉 |
  | `--pico-border-radius` | `0.35rem` | `0.35rem` | ✅ 生效 |
  | `--pico-font-size` | `90%` | `90%` | ✅ 生效 |

- **根因（推断，待验证）**：Pico v2 把**颜色**变量声明在更高优先级的主题块里（形如 `:root:not([data-theme=dark])`，特异性 0,1,1），而 `pico.custom.css` 用朴素 `:root`（0,1,0）→ 无论加载顺序都输给 Pico。`--pico-border-radius` / `--pico-font-size` 在 Pico 侧也是朴素 `:root` 声明（0,1,0），同级下**后加载者胜** → 自定义表恰好生效。这解释了「为什么只有颜色没生效」，也是问题长期隐蔽的原因。
- **影响面**：全站组件配色停留在 Pico 默认蓝（按钮、链接、表单焦点等）。页面背景的暖色来自 `app.css` 而非主题变量，进一步掩盖了症状。**这也意味着任何新写的、依赖 `pico.custom.css` 颜色 token 的样式都会静默取到与设计意图不同的值**——本次规则页组头的深底就是这种情况（幸而 Pico 默认的 contrast 底也是深色，白字仍达 ~15:1，未出可读性问题）。
- **修法候选**（择一，需全站视觉走查后决定）：
  1. 把自定义表的 `:root` 改成与 Pico 同特异性的选择器（如 `:root:not([data-theme=dark])`），或直接 `[data-theme=light]`/`html` 提升特异性；
  2. 给 `<html>` 显式加 `data-theme="light"` 并让自定义表用 `[data-theme=light]` 覆盖；
  3. 用 CSS `@layer` 控制层序（改动面最大）。
- **风险**：修好后**全站配色会立刻从默认蓝变成鼠尾草绿系**，属大范围视觉变更，必须逐页走查后再启用，不可顺手改。
- **Status（2026-09-20）**：已定位并留证，**未修**（超出当次「规则页 4 项 UI 修正」范围，且属全站级变更）。

## T11 — Pico 按钮下边距触发器：同一 flex 行混用「有/无 type」按钮会错位（余 33 处待核查）

- **What**：**Pico 只给带显式 `type` 属性的 `<button>` 加 `margin-bottom: var(--pico-spacing)`（14.4px）**；不带 `type` 属性的不加。因此当同一个 flex 行里既有 `type="button"` 又有无 `type` 的按钮时，前者所在列被撑高 14.4px，行内 `align-items:center` 会转而把矮的那一列**居中** → 视觉上前者偏上 **7.2px**（= 14.4/2）。
- **实测证据**（浏览器隔离实例）：交换两个按钮的 `type` 属性，边距随之交换（0px ↔ 14.4px，可逆）；把控制行内按钮下边距清零后，三者 `top` 从 `92.16 / 84.97 / 84.97` 全部变为 **84.97**，偏移 −7.2 → **0**。
- **已修**：规则页控制行（`rules_list.html` 加 `.rules-controls button { margin:0 }`）。规则页另两处（`.row-actions`、`.rule-group-head`）此前已清零。
- **余留**：全模板另有 **33 处** `type="button"`，若与无 `type` 按钮同行则可能同类错位。已知 `review_tabs.html` 的三个 Tab 全部带 `type="button"` → 彼此一致，无相对偏移，**当前无问题**；其余需在**实际出现错位时**逐个核查，不做无差别预防性清零（会改变各页既有间距，风险大于收益）。`review_clause_panel.html:67` 已有 `style="margin:0"` 的逐处补丁，即本坑的历史痕迹。
- **排查方法（重要）**：**不能只比 `height`**——两个按钮高度本来就都是 44.19px，看起来"等高"。必须比 **`getBoundingClientRect().top`**（或 `centerY`）。本次首版修复就是只量了高度、误判为已对齐。
- **Status（2026-09-20）**：规则页已修；余项触发式核查。



## T12 — 流式中断的服务端语义（deferred，2026-09-23 plan-eng-review 后登记）

- **What**：明确 `/qa/ask` 流式路径在**客户端中断**（关标签页、点「＋新会话」、切会话、网络断）时的服务端行为：会话是否保留、部分答案是否入库、`qa_request_logs` 是否补记。
- **Why**：现状未定义。首轮中断可能留下**空会话**（已 `create_session` 但 `_finish_turn` 未执行）；且前端 `_fallbackAsk` 会重发同一问题，导致**同一次提问发起两次完整 LLM 生成**（双倍成本）。
- **Context**：来源为 2026-09-23 的 `/plan-eng-review` 外部评审（Codex）指出。相关代码：`app/routes/qa_routes.py` 的 `_sse_stream`、`static/components/qa.js` 的 `_streamAsk` / `_fallbackAsk`。注意前端已有 `AbortController` 之类机制缺失——目前没有主动取消 reader 的路径。
- **Pros**：消除“空会话”脏数据；避免重复计费；中断后的状态可预期。
- **Cons**：需要定义并测试「中断」这一难以稳定复现的场景；可能要给前端加 `AbortController`。
- **Blocked by**：无。建议在流式实际投用、观察到中断问题后启动。

## T13 — CLI 后端取消（deferred，2026-09-23 登记）

- **What**：评估并移除 `CLIBackend`（`claude` / `codex` 子进程）后端，统一走 API 后端。
- **Why**：CLI 后端在 Windows 下依赖外部进程与登录态，是额外的运维面；`APIBackend` 已实现 `classify_batch_sync`，功能上可覆盖三个调用点。
- **Context**：调用点仅三处——`app/ai/classifier_ai.py:20`（AI 分类）、`app/routes/import_routes.py:113`（导入时的版本校核）、`app/routes/qa_routes.py`（QA）。另需清理设置页的后端选择 UI 与 `ai.backend*` 三个配置项。**另注**：`CLIBackend._run_cli` 用同步 `subprocess.run`，在 async 路径里会**阻塞事件循环**（最长 60s）——若保留 CLI 后端，这是个独立待修项。用户 2026-09-21 表示“后续可能考虑取消”。
- **Pros**：去掉一层外部进程依赖；消灭事件循环阻塞风险；减少设置项。
- **Cons**：失去“用户已有 CLI 订阅、无需另配 API key”这条路径。
- **Blocked by**：无硬依赖。需先确认实际使用中是否有人依赖 CLI 后端。

## T14 — 模型安装期可选化（deferred，2026-09-23 登记）

- **What**：封装/分享时把 CrossEncoder 精排模型（`bge-reranker-base`）设为**可选安装项**，安装流程中给出提示与跳过选项，并说明缺失后果。
- **Why**：分享给他人时对方可能不装精排模型；当前系统会**静默降级**，用户不知道检索质量为何下降。
- **Context**：设计文档 D12 已在本轮补上「降级状态透出前端」与「健康检查报告模型就绪」两项可观测性，但**安装期**的可选化属于封装方案范畴。相关代码：`app/ai/reranker.py`、`app/ai/embedding.py`（均已是 `local_files_only=True`，不会自动下载）、`app/maintenance/health_check.py` 的 `model_ready` 项。
- **Pros**：对方拿到系统后知道缺什么、要不要装；降低分享门槛（可不装精排模型先跑起来）。
- **Cons**：需设计安装脚本/文档；embedding 模型缺失会让向量召回一并失效（比精排更严重），可选化的边界要分清。
- **Blocked by**：封装方案立项之后（用户 2026-09-21：“等封装的时候再谈”）。

## T15 — 维度名硬编码第三处：检索缓存键 `_cache_key`（2026-09-23 由 T13 复核发现并登记）

- **What**：`app/search/hybrid_search.py:37-44` 的 `_cache_key()` 把 8 个维度名**逐个硬编码**（`tuple(query.dim1_hierarchy or ())` … `tuple(query.dim6_material or ())`）。这是同一组维度名在仓库里的**第三处**（另两处：`app/models.py` 的 `QA_DIM_FIELDS`、以及 QA 路由的维度构造，均已收敛到常量）。
- **Why**：**静默错误风险**——将来若新增第 9 个维度，而有人只改了 `SearchQuery`/`QA_DIM_FIELDS`/QA 路由、漏改 `_cache_key`，那么**两组不同筛选会算出同一个缓存键**，第二次请求会命中第一次的缓存并返回**另一组筛选的结果**。它不报错、不告警，只是安静地给错结果（本项目已有「缓存键漏项 → 跨库污染」的先例，见 `hybrid_search.py:28` 的注释）。
- **Context**：QA 侧已有守卫用例 `tests/test_qa_relax.py::test_qa_dim_fields_matches_request_model`（钉 `QA_DIM_FIELDS` ↔ `QaRequest` 字段集一致），但**检索侧没有任何守卫**。本项超出 QA 计划范围（该计划不新增维度），故登记而非顺手改。
- **建议修法**（择一）：
  1. 让 `_cache_key` 从**单一来源**派生维度项（例如从 `SearchQuery` 的 dataclass 字段中筛出 `dim*` 前缀字段生成元组），彻底消除手工列举；
  2. 或加一条一致性守卫用例：`SearchQuery` 的 `dim*` 字段集合 == `_cache_key` 实际纳入的字段集合（需让后者可被测试观察到，例如抽出一个 `_cache_dim_items(query)` 纯函数）。
- **Pros**：消除一类"加维度即静默串味"的隐患；把三处硬编码收敛到一处。
- **Cons**：动检索层核心路径，需跑检索相关全量用例；修法 1 会改变 key 的构成（缓存键变化只会导致一次冷启动，无正确性风险）。
- **Blocked by**：无。与本轮 QA 计划解耦，可独立立项。

## T16 — `_finish_turn` 的事务边界（2026-09-23 由 T15 收尾轮实现者发现并登记）

- **What**：`_finish_turn`（`app/routes/qa_routes.py`）是「`create_session` → append 用户消息 → append 助手消息 → `_emit_trace`」四步。若在**半途**抛异常（会话已建、用户消息已落库、助手消息写失败），当前会发 `error` 帧并落一条埋点，但**库里留下一条只有用户消息的半截会话**。
- **Why**：这是注释里那条「失败轮次不入库」在**异常路径**上的反例——正常失败路径（`persist_ok=False`）确实不入库，但**异常**路径会留下半截数据，且用户回看历史时会看到一条「只有提问、没有回答」的会话。SQLite + `get_db()` 每次新连接 ⇒ **无嵌套事务**，故三次写不在一个事务里。
- **Context**：来源为 Task 15 收尾轮实现者的上报（不在该轮裁决范围内，故未处理）。相关代码：`app/routes/qa_routes.py` 的 `_finish_turn`、`app/qa/sessions.py` 的 `create_session`/`append_message`、`app/database.py` 的 `get_db()`。
- **Pros**：消除半截会话脏数据；让「失败轮次不入库」在异常路径上也成立。
- **Cons**：要给 `get_db()` 或该调用点引入显式事务（`BEGIN`/`COMMIT`/`ROLLBACK`）语义，牵动既有连接管理；需评估与既有 `with get_db()` 用法的一致性。
- **Blocked by**：无。属独立小项，但会动到持久化层，建议单独立项、单独评测。
