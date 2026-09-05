# 术语 / 分类权威词典设计（termdict）

日期：2026-09-05
状态：待评审
关联系统：施工规范智能查询平台 V2（FastAPI + HTMX + SQLite FTS5 + LanceDB）
上游：TODOS T3（承接 [[2026-09-04-lexicon-module-design]] 词库子系统之后的另立项）

## 一、背景与目标

dim4/5/6（专业/部位/材料）以 clause 粒度标注，走「规则引擎命中 → 低于阈值入队列 AI 分类 → 人工审核」链路。现状调查（2026-09-05 代码 + dev 库只读核查）确认三处痛点：

1. **无权威枚举**：系统不存在「维度 → 合法候选标签」的集中定义。`collect_label_candidates` 从 `classification_rules` 的 **active pattern** + `clauses` 现值动态拼候选（`app/classifier/batch_queue.py`），AI prompt 允许新建标签 → dim4/5/6 标签是「种子词 + AI 半监督 + 人工确认」的**开放演化集**。
2. **碎片污染**：AI 自动采纳（auto_adopted）把 `extract_keywords` 提取的词直接沉淀为规则（`bump_rule`，`new_rule_active=True`），dev 库 dim4=156 / dim5=190 / dim6=119 条规则**全部 active**，含 `style/td/word`（HTML/Markdown 残留）与 `检验/试验/验收`（通用动作词）等 `confirmed=0` 碎片，命中即打错误标签。这些词被规则页「僵尸规则」逻辑豁免不到（locked=0 但持续命中），cleanup 脚本只能事后清理。
3. **label 与 dimension 二元组已散落在规则表内**，但无独立词表层承载「术语词面 → 权威标签」的映射，也没有供 AI classify prompt 参照的干净权威标签清单。

**目标**：新建 **术语 ↔ dim4/5/6 维度标签权威映射词典**（下称「词典 / termdict」），成为该维度**唯一合法标签源（收口）**，服务规则引擎与 AI 分类三条消费：收口标签（开放造词降级为人工背书）、供给干净权威候选清单（降幻觉标签）、为规则补干净 pattern（TODO T3 原文「自动补 classification_rules pattern」）；词面兼作未来 jieba userdict 词源。

### 关键决策（均与用户确认）

| # | 决策 | 语义 |
| --- | --- | --- |
| D1 | **约束强度 = 收口为主 + 白名单例外入人工** | 词典 = 该维度唯一合法标签源。规则/AI 产出的 label ∉ 词典 → **一律转 review**（人工背书），不直接采纳。人工确认后「打标 + 补词典 + 沉淀干净规则」一次完成。 |
| D2 | **初始种子 = 存量干净规则迁移打底** | seed `locked=1` + `confirmed>0 且 label 非空` 规则 → 聚成初始词典；`confirmed=0` 碎片（label ∉ 词典）批量停用（保留供审计，不删）。词典上线即有覆盖。 |
| D3 | **架构 = 方案 A（词典为规范上游，规则表仍是执行层）** | 词典词条确认时同步 upsert 一条干净 keyword 规则（pattern=canonical, label=权威值）；分类执行路径不变，收口校验只插在四处闸门。不做「词典词面直接参与 classify 匹配」（方案 B）——重构匹配/统计/规则页，收益风险不对称，延后。 |
| D4 | **UI = 独立子页 `/termdict`** | 与 `/lexicon` 平级中栏页，复用词库已验证的交互组件；不并入词库页（检索与分类异域）或规则页（已重）。 |

### 非目标（本期明确不做）

- 词典词面直接参与 classify 匹配 / 淘汰 keyword 碎片规则层（方案 B 方向，见「九、后续方向」）。
- jieba userdict 联动（把词典词面灌入分词词典）。
- 维度范围扩展：仅 dim4/5/6（AI 造词维度）。dim2/3 为规范级、无 AI 队列；dim1 由编号前缀硬编码（`spec_prefix.py`），均不收口。
- 别名第二套文本归一：lexicon 已管 synonym/alias 归一与检索扩展，词典不重复建职责。词典 `aliases` 仅承载「候选清单词面 / prompt 参照词面」。
- AI 自动建词 / 自动消歧 / 同义词自动发现。
- 批量清洗既有 `clauses` 已打标历史值（含碎片值）：保留现场，走 UI 校对而非脚本改。

## 二、使用边界（必须写入代码注释）

1. **白名单**：只有 `term_labels` 中 `is_active=1` 的 `(dimension, label)` 是该维度的合法分类目标值。分类引擎 / AI 采纳 / 规则沉淀三处 label 判定必须以词典为准。
2. **词典是规则的上游，不是规则的替代**：`classification_rules` 保留执行层职责（keyword/regex/exact 匹配 + priority + hit/confirmed 统计 + 规则页人管 + 锁定 + 僵尸判断）。词典词条确认 = 产生一条干净规则 + 权威 label；存量碎片规则停用而非由词典接管。
3. **词面唯一性（同维）**：同一维度内，一个词面（canonical ∪ aliases）**只能指向一个 label**；跨维度允许（`钢筋` dim4→结构 / dim6→钢筋）。冲突 = 候选歧义，宁可停。
4. **缓存失败宽松放行**：词典加载/DB 异常 → 记日志回退「放行」语义（不因词典故障让导入/分类全停）；词面冲突 → 缓存禁载 + 告警。
5. **失效边界**：词典写操作后 `invalidate_term_cache()`；词典改动**不**联动清 lexicon / 检索结果缓存（维度候选与检索解耦）。collect 候选若无独立缓存则无需额外清。
6. **aliases 语义**：仅作候选清单/prompt 词面与展示，不做归一替换（归一已由 `lexicon.normalize.normalize_text` 在 `classify_clause` 前完成）。

## 三、现状与改动落点（核实结论）

| 现状 | 本次改动 |
| --- | --- |
| `collect_label_candidates`（batch_queue.py）从规则 pattern + clauses 现值拼候选，AI 可新建 → 开放造词 | 候选主源改为词典（见「六.5」）；AI 新建标签需人工背书 |
| `rule_engine.classify_clause` 命中 label 即写列（import_routes.py 主循环 / apply_ai_results） | 命中 label 过白名单闸门：∉ → 不写列、转 review（见「六.1」「六.2」） |
| `bump_rule`（rule_sink.py）新规则直接启用（auto_adopted 传 new_rule_active=True） | 新规则 label ∉ 词典 → 强制不启用（见「六.3」） |
| `process_feedback` / 审核页确认：写列 + bump 干净规则 | 确认时**追加** `term_labels` upsert（新词入典，source=review），构成「人工背书扩字典」（见「六.4」） |
| 无任何集中 label 定义 | 新表 `term_labels` + 新包 `app/termdict/`（见「四」「五」） |
| 无词典维护入口 | 新独立子页 `/termdict`（见「八」） |
| seed 锁定规则（locked=1）+ confirmed>0 干净规则散落在规则表 | 一次性迁移脚本聚成初始词典 + 停用碎片（见「七」） |

## 四、数据模型

`term_labels` 单表，追加进 `SCHEMA_SQL`（幂等 `IF NOT EXISTS`，新库老库同源建表）：

```sql
CREATE TABLE IF NOT EXISTS term_labels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension  TEXT NOT NULL,                 -- dim4 | dim5 | dim6
    label      TEXT NOT NULL,                 -- 权威标签值（写入 clauses.dimX_* 的就是它）
    canonical  TEXT NOT NULL,                 -- 代表术语词面（迁移取该 label 下 hit 最高干净 pattern）
    aliases    TEXT NOT NULL DEFAULT '',      -- 同义/俗称词面，逗号分隔；仅候选/prompt 用
    source     TEXT NOT NULL DEFAULT 'manual',-- seed | migrate | review | manual
    note       TEXT NOT NULL DEFAULT '',
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
-- 权威行键 = (dimension, label)：label 是该维度的合法目标值，天然唯一。
-- canonical/aliases 的词面在「同一维度内只能指向一个 label」由应用层一致性校验
-- （跨 canonical+aliases 列与跨行全局检查，仿 lexicon equiv_conflict），不落 DB 约束。
CREATE UNIQUE INDEX IF NOT EXISTS idx_term_labels_dim_label ON term_labels(dimension, label);
```

建模规则与语义：

- **label 是行键**：一行 = 一个权威标签 + 它的代表词面（canonical）与可选同义词面（aliases）。白名单 = `SELECT DISTINCT label FROM term_labels WHERE dimension=? AND is_active=1`。
- **一个 `(dim, label)` 只有一个 canonical**：若人工确认新词时 canonical 词面已存在于同一 `(dim, label)` 的 aliases（或反之），**归入 aliases 合并而非另起行**；只有当 `(dim, label)` 不存在才新增行。语义统一仿词库「组唯一是应用层约束」。
- **跨维度同词允许**：`钢筋` 可同时在 dim4→结构、dim6→钢筋 两条（各自是不同 label 的 canonical/alias），一致性校验只在维度内比较。
- **word 面子串防护**：同维度内不同 label 的 canonical/aliases 不得互为子串（防 keyword 匹配自误报，仿词库 validation 已有规则）。不同 label 共享词面但非子串（如 `混凝土` 与 `钢筋混凝土`）允许。

### 与 lexicon 的边界

lexicon（检索归一 / 词库 CRUD）与 termdict（分类标签收口）是**两套数据与消费链**，异源并存、互不写对方。lexicon 的 synonym/alias canonical 未来可作为 termdict 词条录入的候选提示源（UI 建议），不做自动同步与引用完整性。

## 五、核心包 `app/termdict/`

新建独立包，与 `app/lexicon/`、`app/classifier/` 平级，职责单一、可独立单测、供多消费方（规则引擎 / 队列 / 审核 / 路由）复用：

```
app/termdict/
├── __init__.py        # 导出 store.load_* / is_valid_label / valid_labels / invalidate_term_cache
├── store.py           # DB 读取 + 模块级 TTL 缓存（DATABASE_PATH 守卫）+ 词面唯一一致性校验 + 失效出口
└── validation.py      # 行级校验（路由 / CSV / 审核确认共用单一来源）
```

### 5.1 `store.py`

接口（初版，供 plan 细化）：

- `valid_labels(dim: str) -> list[str]`：active 权威 label 清单（白名单，prompt 候选主源）。
- `is_valid_label(dim: str, label: str) -> bool`：label ∈ 白名单（去空白后比较）。**词典不可用（加载失败 / 词面冲突 / valid_labels 为空）时恒返回 True（放行）**——规则命中与 AI 采纳不被词典故障阻断，放行语义只收敛于此，四处闸门不各自判断。
- `load_active_entries(dim: str | None) -> list[TermLabelRow]`：词条行（UI 列表 / 迁移核对）。
- `invalidate_term_cache() -> None`：写后唯一失效出口（清模块缓存；延迟联动凡需刷新处）。
- 缓存形态复用 lexicon：模块级 `_cache` + `_cache_ts` + `_cache_path == DATABASE_PATH` 守卫，TTL 60s。写路径（routes / 审核确认 / 迁移）后必须调 invalidate。
- 一致性：加载后跑词面唯一校验（见 S2），通过才填缓存；冲突置 `word_conflict`（仿 `lexicon.equiv_conflict_word`）并缓存置空 + WARN。

### 5.2 `validation.py`

- `validate_dim(dim)` / `validate_row(dimension, label, canonical, aliases, note)` → (净数据 dict | None, 错误信息 str | None)。
- 校验项：dim ∈ {dim4,dim5,dim6}；label/canonical 非空；aliases 拆词去空白、元素 ≠ canonical、不与 canonical 互为子串；canonical/aliases 与「同维度内他行已存词面」不得互为子串（子串防护需查库：validate_row 在函数体内经 store 读「同维词面快照」，单一来源；路由/CSV/审核确认共用同一函数，**不接受外部快照参数**——防调用方传入过期快照）。

## 六、收口数据流（四处闸门 + 候选清单）

四处均为「拦截转 review / 不启用」，**从不静默丢弃**，与 D1「新词人工背书」一致。

### 6.1 闸门①：规则命中（导入/分类主循环写库点）

位置：`import_routes.py` 条文分类主循环写 clauses 处（规则命中得 `best_label`，dim4/5/6 各维）。

- `is_valid_label(dim, label)` ✓ → 照旧写 `clauses.dimX_*` + 命中规则 `hit_count+1, confirmed+1`。
- ✗（碎片命中 `style→结构`，或 `label IS NULL` 回退 pattern）→ **不写列、不累命中规则统计**，`try_enqueue` 转 `review`（`keyword_score` 保留作人工参考）。
- 存量影响：confirmed=0 碎片命中将全部转 review —— 这是「七、迁移」停用碎片规则的核心动因；迁移上线后碎片规则 `is_active=0` 不再命中，review 面收敛为「词典尚未覆盖的真实新词」。

### 6.2 闸门②：AI 自动采纳（`batch_queue.apply_ai_results`）

auto_adopt 条件追加 `is_valid_label(dim, ai_label)`：置信度 ≥ `ai_confidence_threshold` 但 label ∉ 词典 → **不 auto_adopted**，降级 `review`（不写库、不沉淀规则）。

### 6.3 闸门③：规则沉淀（`rule_sink.bump_rule`）

新规则 INSERT 时 label 过白名单：label ∉ 词典 → 强制 `new_rule_active=False`（`is_active=0` 落库、可查），**除非带人工信号**（`is_confirmed=True` 的人工确认路径已由 6.4 保证 label 先入词典）。已存在规则路径不变。

### 6.4 闸门④：人工确认双写（`feedback.process_feedback` 扩展）

审核确认（规则命中 review / AI review 队列）时，`process_feedback` 在既有「写列 + bump 规则」基础上**先**执行词典 upsert：

1. 确认 label ∉ 词典 → `term_labels` 新增 `(dim, label)`，canonical=确认标签词面（若提取关键词等于 label 则 canonical=label），source=review。
2. label ∈ 词典但 canonical 词面是新增变体 → 并入该行 aliases（词面唯一性校验通过前提）。
3. 然后才写 `clauses` 列 + `bump_rule`（此刻 label 已在词典，闸门③放行，带 confirmed+1 正常启用）。
4. `invalidate_term_cache()`。

驳回路径（`reject_review`）不改词典、不沉淀规则。

### 6.5 候选清单（`batch_queue.collect_label_candidates`）

- 主源改为词典：`valid_labels(dim)` 全给（权威），并对每个 label 附加其 canonical/aliases 词面（让 AI 在「选权威标签」的同时能匹配到条文词面）；词面去重保序。
- `clauses` 现值兜底**仅当**词典 `valid_labels` 为空（首启异常态）。
- `_cache_key` 若含候选维度需核对（词库与参数项目经验：候选进 prompt 不进检索缓存，通常无需清）。

## 七、迁移与初始打底

`scripts/migrate_term_dict.py`（幂等，可重复跑；参照 `scripts/seed_rules.py` / `cleanup_rule_pollution.py` 形态）：

1. **聚合初始词典**：`classification_rules` 中 `dimension IN (dim4,5,6)` 且 `(locked=1 OR confirmed>0)` 且 `label IS NOT NULL AND label!=''` → 按 `(dim, label)` 分组去重，canonical 取该组 `hit_count` 最高的 pattern（pattern 需过词面子串清洗，空/过脏跳过并记日志）；`source=migrate`。写入走应用层校验，冲突行记日志跳过（不阻断）。
2. **停用碎片**：`confirmed=0 且 label 非空且 label ∉ 新词典` → `is_active=0`（保留供审计）；seed `locked=1` 的规则 label 必进词典（第 1 步已含），不受影响。
3. 清理 `label IS NULL` 的回退型规则不在本期批量动（它们命中即走闸门① review，属正常人工背书面）。
4. **不触碰** `clauses` 历史打标值。

迁移后核对报表：入典 `(dim,label)` 数 / 停用碎片数 / 跳过冲突数；dev 库先跑一次验证，结果进 spec 评审。

## 八、UI —— 独立子页 `/termdict`

新建 `app/templates/termdict.html`（词库页同型：内联脚本 + Alpine kind/dim 状态 + hx 片段），挂进中栏导航（与词库入口同级）。能力清单：

- **按维度 Tab 分屏**（dim4 专业 / dim5 部位 / dim6 材料），Tab 驱动列表与新增表单（复用词库 kind Tab 模式）。
- 列表列：权威 label / canonical / aliases / source / note / 启用状态；操作列：编辑 / 启禁 / 删除（删除走项目内 `.dialog-overlay` 确认，禁用原生 confirm —— 沿用词库/参数页规范）。
- **新增 / 行内编辑**：`dimension,label,canonical,aliases(逗号),source,note`；label/canonical 必填；写前走 `validation.validate_row`，后端校验失败返回行内错误（`aria-invalid` 红框 + 提示文案，沿用参数页失焦校验风格）；重复 `(dim,label)` / 词面子串冲突返回 400 文案。
- **冲突全局告警**：store 词面唯一性冲突置 `word_conflict` 时，页顶红条提示冲突词（仿 lexicon equiv 冲突提示模式）。
- 每次写操作 `HX-Trigger: termdictUpdated`，列表 `hx-trigger="load, termdictUpdated from:body"` 自动刷新（复用词库已验证刷新链）。
- 规则质量联动提示（可选，本期只做展示不做强制）：`/rules/quality` 报表侧标注「词典外 label 命中」计数入口，供管理员跳转校对。

## 九、错误处理与一致性

| 场景 | 行为 |
| --- | --- |
| 词典 DB 加载失败 / 异常 | 记 WARN，缓存置空 → 按 §5.1「词典不可用恒放行」：`is_valid_label=True`、`valid_labels=[]`（候选退化为现值兜底）。 |
| 词面唯一性冲突（同维词面指两 label） | 缓存禁载 + `word_conflict` 置词 + WARN；闸门行为同 §5.1（恒放行），UI 突出冲突告警，修复后经 invalidate 重载。 |
| label 为空 / NULL 回退 pattern 的旧规则 | 不在词典 → 走闸门①转 review；不额外处理。 |
| AI 返回非法 JSON / 空 label | 沿用队列现有容错，不影响词典。 |
| 并发写 | 单 SQLite 顺序写；词典行 upsert 定用 `INSERT ... ON CONFLICT(dimension,label) DO UPDATE`；canonical/aliases 词面并入合并由校验层（词面唯一 + 归属 label）承担（§5.2 / §6.4），写入后一律 `invalidate_term_cache()`。 |

缓存失败「宽松放行」与冲突「回到现状」在测试中以注入 store 异常/冲突覆盖。

## 十、测试（TDD）

新增测试文件（沿用词库 test 组织 `tests/test_termdict*.py`），用例覆盖**正常 / 边界 / 异常**三类：

- `test_termdict_store.py`：valid_labels / is_valid_label（含空白、大小写边界）；加载失败宽松；词面冲突禁载与 `word_conflict`；invalidate 后重载；DATABASE_PATH 换库不串缓存。
- `test_termdict_validation.py`：维度合法；label/canonical 空拒；aliases 拆词/去重/自反/子串拒；同维跨行子串拒；跨维同词允许。
- `test_termdict_gates.py`（改动点回归）：
  - 闸门① 规则命中 label ∉ 词典 → 不写列、不累统计、入 review 队列；∈ → 照旧。
  - 闸门② AI ≥0.7 但 label ∉ → 降 review，不 auto_adopted、不沉淀。
  - 闸门③ bump_rule 新规则 label ∉ → is_active=0 落库；∈ / 人工确认 → 启用。
  - 闸门④ 人工确认新 label → 词典 upsert + 写列 + bump（confirmed+1）；驳回不改词典。
  - collect_label_candidates 词典优先 / 词典空回退现值兜底。
- `test_termdict_migrate.py`：幂等重跑；入典计数；碎片停用判定；冲突跳过不阻断。
- UI headless 冒烟：/termdict 增删启禁 + 冲突提示（一次脚本，跑完即删）。

预估 ~35–45 例（以词库测试 641 体系为基线增量）。

## 十一、后续方向（不在本期）

- **方案 B 收敛**：待词典覆盖稳定后评估「词面直接参与 classify 匹配、keyword 碎片规则层退场」——需重构匹配/统计/规则页，另立项。
- **jieba userdict 词源**：把 canonical/aliases 灌入分词词典（T1 相关，先验证索引侧 OOV）。
- **维度扩展**：dim2/3 若后续引入规范级 AI 分类，复用同一白名单机制。
- **AI 辅助建词**：把「候选清单以外新词」自动聚成待确认草稿（半自动入典），本期不碰。
- **与 QA/词库联动**：术语→标签权威映射亦可作 QA 术语解释来源（另议）。

## 十二、风险与开放项

- **seed 阈值失同步**：`scripts/seed_rules.py` 当前规则 threshold 与 dev 库 locked 种子不一致（脚本 0.25 vs 库 0.6/0.7）。迁移打底只依赖 `locked=1`/`confirmed>0`/label，**不依赖 threshold**，不受影响；是否重跑 seed 到与库一致另行处理，不在本期。
- **clauses 维度列 distinct 值异常少**（dim4=3/dim5=5/dim6=6）：疑与最近重跑/清理有关，属数据审计，本期不处理，迁移核对时顺带核对一次列值分布，异常则报出。
- **闸门①对存量高命中碎片的影响**：confirmed=0 碎片迁移即停用，命中面收敛；高 confirmed 规则的 label 已入词典不会误拦。真实新词转 review 面由人工背书，人工量以迁移后核对报表实测。
- **canonical 与 label 是否恒相等**：不等场景（如 canonical=钢筋 → label=结构）允许且常见；UI 需明确区分「词面」与「标签值」两列语义，防录反。
