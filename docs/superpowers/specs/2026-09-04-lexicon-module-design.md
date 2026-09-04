# 词库子系统设计（同义词 → 词库升级）

日期：2026-09-04
状态：待评审
关联系统：施工规范智能查询平台 V2（FastAPI + HTMX + SQLite FTS5 + LanceDB）

## 一、背景与目标

现有 `synonym_map`（source→target 单向映射）只被规则引擎在条文匹配前做归一化（`rule_engine.py::normalize_text`），检索链路完全未使用。本次将「同义词」升级为**词库子系统**：词库 = 同义词 / 别名 / 易混淆术语 三类，并让词库真正服务三条消费链路：**检索查询扩展（仅 FTS5）、规则引擎归一化、RAG 问答的易混淆命中提示**。

### 词库定义

| kind | 语义 | 落库模型 | 检索 | 规则归一化 | 易混淆提示 |
| --- | --- | --- | --- | --- | --- |
| `synonym` | 语义完全等价，可互相替换 | canonical + variants 多词组 | token OR 扩展 | variant→canonical | 不参与 |
| `alias` | 规范术语 ↔ 工地俗称/简写/异体字，语义等价 | canonical（规范词）+ variants（俗称） | token OR 扩展 | variant→canonical | 不参与 |
| `confusable` | 语义相近但概念不同 | 一行一对（canonical=A, variants=B, distinguish） | **绝不扩展** | 不参与 | 命中检测 → 提示框 |

> **关键决策（与用户确认）**：`synonym` 与 `alias` 在消费端**不做区分**——检索扩展与规则归一化对二者走同一套「以组内词集扩展、向 canonical 归并」逻辑；`kind` 仅承担 UI 分区、种子语义标签与将来可能的分治。理由：检索扩展双向均有意义（输入任一词都能召回含组内其它词的条文）；归一化恒向 canonical，synonym 组选定 canonical 后同样成立。若确有必须严格单方向（如 alias 输入规范词不反查俗称）的场景再行拆分。

### 非目标（本期明确不做）

- jieba userdict 联动（把词库词灌入分词词典）。与检索扩展是正交机制，见「九、后续方向」。
- 把易混淆命中提示注入 LLM prompt / 用 LLM 做自动消歧。提示是给用户看的。
- confusable 查询改写、同义词自动发现、AI 辅助建词。

## 二、使用边界（必须写入代码注释）

1. **同义词 / 别名只做 BM25/FTS5 关键词检索扩展**；向量检索与 embedding 输入**不替换原始词**——向量靠 embedding 本身语义理解，强行替换引入噪声。该约束由测试锁定：扩展前后向量输入文本逐字节不变。
2. **易混淆术语只做命中告警，绝不做检索改写**，防止把两个不同概念检索混在一起。
3. 种子词库只是起点；WebUI 支持后续项目使用过程持续补充行业术语。
4. 词库同时服务于：检索查询扩展、规则引擎匹配、RAG 问答预处理（易混淆命中提示）。

## 三、现状与改动落点（核实结论）

| 现状 | 本次改动 |
| --- | --- |
| `synonym_map` 仅 4 类引用：`rule_engine._load_active_synonyms`（唯一运行时读点）、`synonym_routes` CRUD + 3 模板 + `main.py` 挂载、`init_db` 建表 + 两行种子、3 个测试文件 | 弃用并**删除**整条链路（见「六、迁移与删除」） |
| `hybrid_search` → `sql_search`（FTS5 jieba 预分词 `build_match_query`，AND 无结果降级 OR）+ `vector`（LanceDB，embed 原文）。检索结果序列有 LRU+TTL 60s 缓存，`_cache_key` 现含 keyword 原文与 `ce_rerank` | FTS 分支增加词条感知切词 + OR 组扩展；向量不动；`_cache_key` 增补扩展开关维度；词库变更须 `clear_search_cache()` |
| `qa_ask` 用问题原文 `hybrid_search` 召回 → 过滤/精排/分层 → context → 后端，返回 `QAResponse(answer, sources)` | 对问题原文做 confusable 检测，`QAResponse` 增 `confusable_hits` 字段 |
| `synonyms.html` 无编辑功能、无 CSV 导入；`maintenance.html` 已有 `x-data tab + x-show` 多 Tab 模式可复用 | 新建 `/lexicon` 三 Tab 词库页，增删改 + 启禁 + CSV 导入 |
| jieba 纯默认词典，无 userdict | 本期不动 |

## 四、数据模型

`lexicon_entries` 单表 + `kind` 列分区（已与用户确认），追加进 `SCHEMA_SQL`（幂等 `IF NOT EXISTS`，新库老库同源建表）：

```sql
CREATE TABLE IF NOT EXISTS lexicon_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,              -- synonym | alias | confusable
    canonical    TEXT NOT NULL,              -- synonym/alias: 代表/规范词；confusable: 词 A
    variants     TEXT NOT NULL DEFAULT '',   -- 逗号分隔等价/俗称词；confusable 恒为单元素=词 B
    distinguish  TEXT NOT NULL DEFAULT '',   -- 仅 confusable 填：A 与 B 的区分说明
    note         TEXT NOT NULL DEFAULT '',   -- 备注/来源
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    updated_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lexicon_kind_canonical
    ON lexicon_entries(kind, canonical);
```

建模规则：

- **synonym / alias**：`variants` 逗号分隔，支持 N 词等价组。alias 的 canonical 即规范词，variants 即俗称/异体字/简写。UI 与注释用对应文案区分。
- **confusable 一行一对**：`canonical`=词 A、`variants` 恰一个词 B、`distinguish` 必填描述 A 与 B 的区分。同一 A 与多个词混淆就拆多行——保证 `distinguish` 语义无歧义，命中检测整行取出。
- **行级校验**（路由与 CSV 导入共用，写成公共函数）：kind 必为三者之一；canonical 非空；synonym/alias 的 variants 非空且不含 canonical 自身（拒绝自反）；confusable 的 distinguish 非空、variants 恰一个词且 ≠ canonical。空 variants 等价于没有组，直接拒绝。

### 与现有表的取舍

不再保留任何 source→target 两列模型；`synonym_map` 表、索引、种子、路由、模板、测试在收尾单元整体移除（见「六」）。

## 五、词库核心包 `app/lexicon/`

新建独立包承载词库数据访问与三类纯逻辑，与 `search/`、`classifier/` 平级，职责单一、可独立单测、供多消费方复用：

```
app/lexicon/
├── __init__.py        # 导出 store.load_*、invalidate_lexicon_caches
├── store.py           # DB 读取 + 模块级 TTL 缓存 + 失效（含检索缓存联动）
├── expand.py          # 检索查询扩展：词条感知切词 + OR 组 MATCH 串
├── normalize.py       # 条文归一化：variants → canonical（rule_engine 数据源切此）
└── confusable.py      # 易混淆命中检测（纯函数）
```

### 5.1 `store.py`

- 常量 `KIND_SYNONYM="synonym" / KIND_ALIAS="alias" / KIND_CONFUSABLE="confusable"`；`EQUIV_KINDS = (KIND_SYNONYM, KIND_ALIAS)`。
- `LexiconRow`（dataclass）：`id, kind, canonical, variants: list[str], distinguish, is_active`；`row_variants()` 负责拆分逗号串。
- 模块级缓存仿 `rule_engine` 现有同义词缓存：`_cache / _cache_ts / _cache_path`，TTL 60s，`DATABASE_PATH` 变化即失效（测试换临时库不串数据）；表不存在/连接失败一律回退空列表，不影响消费主流程。
- 访问器（均只取 `is_active=1`）：
  - `load_equivalent_groups() -> list[LexiconRow]`：kind ∈ EQUIV_KINDS，供 expand / normalize。
  - `load_confusable_pairs() -> list[LexiconRow]`：kind = confusable，供命中检测。
  - `load_all() -> list[LexiconRow]`：全部 active（词库管理 UI 自身不走缓存而直查 DB，见「八」）。
- `invalidate_lexicon_caches()`：清本模块缓存 + 延迟导入调用 `hybrid_search.clear_search_cache()`（词库增删改/启禁后必须调用，保证检索结果序列不残留旧词条产物）。本函数是**唯一的缓存失效出口**，词库路由 CRUD 一律调它。

### 5.2 `expand.py` —— 检索查询扩展（核心）

设计目标：只改 FTS **查询端**，让用户输入某词时能把等价词一并纳入 MATCH，从而命中「只含组内另一词的条文」；索引文本与向量输入均不改一字。

主函数保持**纯函数**（groups 由调用方注入），便于无 DB 单测：

```
build_expanded_match(keyword: str, groups: list[LexiconRow],
                     join_with: str = "AND") -> str
```

算法（词条感知切词 + OR 组，已与用户确认采用该路径）：

1. 收集词表 `all_terms` = 各组 canonical + 各 variants，按词长降序。
2. 对 `keyword` 做**长词优先扫描**，把命中的词条词从原文中切出（得到一组命中的词条词及其所在组）；剩余未命中片段回退 `app/search/tokenize.tokenize()`（jieba 精确模式，过滤空白/纯标点），保证与索引侧分词一致性。
3. 输出有序「项」列表；每项为候选词集 `list[str]`：
   - 命中某词条词 → 候选集 = 该组 canonical + 全部 variants（OR 组）。
   - 未命中普通 token → 候选集 = `[token]`。
4. 组内候选词 `OR` 连接、项间按 `join_with` 连接，拼成 FTS5 MATCH 串；词与连接符的引号转义规则复用 `tokenize.build_match_query` 的既有实现（每个词 `"…"` 包裹 + 内部引号翻倍），整条返回；keyword 无任何有效词返回空串（与现 `build_match_query` 语义一致，调用方据空串走纯 SQL 分支）。
5. **退化约束**：groups 为空或开关关闭时，输出必须与现 `build_match_query(keyword, join_with)` 逐字节一致——保证旧检索行为与既有 `test_tokenize`/`test_hybrid_search` 用例不回归。

示例：输入「砼强度等级」，命中 alias 组 `混凝土={砼}` → `("混凝土" OR "砼") AND "强度" AND "等级"`（后两个为 jieba 切出的普通词，token 间保持 AND 连接，与原 `build_match_query` 的多词语义一致）。

> 折叠细节：长词优先识别可用「按降序逐词在 text 上扫描 + 已占用 span 跳过」，词库词量级为百~千，query 为短文本，复杂度安全；具体实现不锁死，writing-plans 拆 Task 时定，但必须满足上面 1–5 条可测行为。

### 5.3 `normalize.py` —— 条文归一化

```
normalize_text(text: str, groups: list[LexiconRow]) -> str
```

- 仅对 kind ∈ EQUIV_KINDS 的组生效；confusable 忽略。
- 把 text 中**任一非 canonical 变体词**替换为 canonical（只替换 variants，不反向替换 canonical）。按变体词长降序替换，沿用现 `rule_engine.normalize_text` 防短词破坏长词的逻辑（如先「高强混凝土」后「砼」——实际各 variants 独立降序即可）。
- 变体词与条文文本的替换用字符串 replace，注释写明局限：极短俗词（单字「砼」）在特殊语境可能误伤词内同形字，工程词库语境下可接受。

### 5.4 `confusable.py` —— 命中检测（纯函数）

```
detect_confusable(text: str, pairs: list[LexiconRow]) -> list[dict]
```

- 对每组 confusable：`canonical in text` **且** 存在某 `v in variants` 且 `v in text` → 产出 `{a: canonical, b: v, distinguish}`。
- 命中对象恒为**用户原词文本**（检索页 keyword、QA 的 question），绝不用扩展后文本。
- text 为短文本，O(组数 × 词长) 量级安全；检测不到返回空列表。

## 六、迁移与旧链路删除

### 6.1 `database.py` 变更

- `SCHEMA_SQL`：删 `synonym_map` 建表块 + 其唯一索引；加 `lexicon_entries` 建表块 + `idx_lexicon_kind_canonical`。
- `init_db`：保留结构——`executescript(SCHEMA_SQL)` 对新老库同源建 lexicon 表；在 `_migrate_search_text` 与触发器之后追加**幂等删除**：
  `DROP TABLE IF EXISTS synonym_map; DROP INDEX IF EXISTS idx_synonym_map_source;`
  （置于预置种子之前；已存在库升级时此处把旧表清掉。）
- 预置种子改为直写 lexicon（幂等依赖 `(kind, canonical)` 唯一索引 + `INSERT OR IGNORE`）：
  - 仅预置一条 `('alias', '混凝土', '砼', '', '', 1)`（由原「砼→混凝土」迁来）。其余词条留待 WebUI/CSV 在使用过程中补充——种子只进语义干净的词条。
- **「箍筋→钢筋」按用户决定不迁移、不落库**（语义为子类非同义，直接丢弃）。

> 说明：之所以不用数据迁移脚本来搬运，是因为 `synonym_map` 现仅两条预置种子、无用户数据；DROP 的幂等位置保证老开发库升级即平滑，无需人工干预。

### 6.2 收尾删除单元（一个 Task 内删净）

前置条件：`rule_engine` 已切 lexicon 且相关测试绿。随后一次性：

1. 删 `app/routes/synonym_routes.py`；`app/main.py` 移除其 `include_router`。
2. 删 `app/templates/synonyms.html`、`partials/synonyms_list.html`、`partials/synonyms_row.html`。
3. `partials/tree_panel.html`：`🔤 同义词`（/synonyms）按钮改为 `📖 词库`（/lexicon）。
4. 删 `tests/test_synonym_routes.py`（其覆盖由新 `test_lexicon_routes.py` 承接）；删 `tests/test_database.py` 中 synonym_map 建表/种子幂等用例，改断言 lexicon_entries。
5. 改写 `tests/test_rule_engine.py` 注入结构（见「七」）。
6. 验收：`grep -ri synonym_map app/ tests/ scripts/` 零命中；全量测试绿。

## 七、消费端接线

### 7.1 检索扩展（FTS5）

- `app/search/sql_search.py`：keyword → MATCH 串的两处调用（AND 主路径与 OR 降级）统一改走一个新的薄包装（读开关 + 词库组后调 `expand.build_expanded_match`）：
  - `match` 与 `or_match` 都用同一扩展产物，保证「AND 无结果 → OR 降级」与扩展语义自洽（降级后仍含等价词）。
  - groups 为空（无启用词条）或开关关闭 → 结果与原 `build_match_query` 等价（expand 退化约束）。
- `app/search/hybrid_search.py`：
  - 向量 `vs.search(keyword)` 与 embedding 输入保持原文，**不改一字**（代码注释写明边界 1；测试锁定）。
  - `_cache_key` 增加 `search.lexicon_expand` 当前值维度（与 `ce_rerank` 同理，避免开关切换串缓存）。
- 新增参数 `search.lexicon_expand`（见「九」参数节），作为全局总开关；检索页 UI 不加复选框。

### 7.2 规则引擎归一化（rule_engine）

- 删 `rule_engine.py` 自带的 synonym 缓存/加载/`normalize_text`，改为从 `app.lexicon` 取数：
  - `classify_clause(..., synonyms=None)` 注入参数语义由「source/target 映射行」改为「`lexicon.LexiconRow` 等价组行」；`synonyms is None` 时由 `store.load_equivalent_groups()` 自动加载（复用模块缓存）。
  - 条文与父路径文本归一化统一调 `lexicon.normalize.normalize_text(text, groups)`。
- `synonym_routes` 曾调用的 `clear_synonym_cache` 随之消亡，改由 `store.invalidate_lexicon_caches()` 承担失效职责。

### 7.3 检索页易混淆提示

- `app/routes/search_routes.py::search`：keyword 非空时对**原始 keyword** 调 `confusable.detect_confusable`，结果传模板。
- 提示条渲染在 `partials/result_content.html` 顶部（放 content 而非 list wrapper，保证翻页/刷新每次带出）；空命中不渲染任何内容。
- `detect` 与检索互相独立：检索可能因同义扩展召回更广，但提示只反映原词命中情况。

### 7.4 QA 问答易混淆提示

- `app/models.py`：`QAResponse` 增字段 `confusable_hits: list[dict] = []`。
- `app/routes/qa_routes.py::qa_ask`：对 `question` 原文检测 confusable，结果填回 `QAResponse`（与 answer/sources 同返回）。
- 前端：
  - `static/components/qa.js::send()`：bot 消息对象增 `confusable: data.confusable_hits || []`。
  - `app/templates/partials/qa_panel.html` bot 渲染区：有 confusable 时，在回答正文上方渲染警示块（`a` 与 `b` 并排 + `distinguish` 文案）。提示块内 canonical/variants/distinguish 均用 **`x-text`** 渲染（用户输入内容，杜绝 HTML 注入），不用 `x-html`。
  - 前端不做任何改写，纯展示；触发的按钮为 QA 弹窗标题旁现有来源引用逻辑不动。
- 不改 `build_system_prompt` / 不进 LLM 上下文（非目标）。

## 八、词库管理 WebUI（/lexicon）

复用 `maintenance.html` 的 `x-data tab + x-show` 多 Tab 模式。

- 路由 `app/routes/lexicon_routes.py`（替换 synonym_routes 的挂载点，`/lexicon`）：
  - `GET /lexicon`：词库页（center_content = `lexicon.html`）。
  - `GET /lexicon/list?kind=&active=`：当前 kind 的列表 partial（按 kind 过滤；active 0/1/空）。
  - `POST /lexicon/create`：按 kind 分支字段建词；幂等（唯一索引冲突 → 「已存在」提示 + 不 500）。
  - `POST /lexicon/{id}/toggle`：启禁，返回更新行。
  - `POST /lexicon/{id}/edit` 与 `GET /lexicon/{id}/edit`：行内编辑（升级旧页「无编辑」短板）；canonical 改动撞唯一约束 → 400 友好报错。
  - `DELETE /lexicon/{id}`：删除，返回整表刷新。
  - `POST /lexicon/import`：CSV 批量导入，返回逐行报告。
  - 所有写操作完成后调用 `store.invalidate_lexicon_caches()` 并触发 `lexiconUpdated` HX-Trigger。
- 模板：新建 `lexicon.html`（三 Tab 头 + 添加表单 + 列表容器）+ `partials/lexicon_list.html`、`lexicon_row.html`、`lexicon_edit_row.html`、`lexicon_import_result.html`。
  - 添加表单字段随当前 kind 切换（同 x-data 联动）：synonym/alias = `代表词(canonical) + 变体/俗称(variants, 逗号分隔多个)`；confusable = `词A + 词B + 区分说明(distinguish 必填)`。
  - 列表列随 kind 展示：synonym「代表词 | 等价词」；alias「规范词 | 俗称」；confusable「词A | 词B | 区分说明」。三类共享同一列表 partial 与 row 结构：**后端按 kind 提供列头文案配置，模板只按配置渲染**，避免三套重复列表。
- CSV 格式：首行表头 `kind,canonical,variants,distinguish,note`；confusable 行 variants 为单值词 B。逐行校验（复用行级校验函数），合法行 `INSERT OR IGNORE` 幂等，非法行收集错误；返回 `成功 N 行 / 跳过重复 M 行 / 失败 L 行` 报告 partial。kind 为空时按当前 Tab kind 兜底（便于导入种子时少写一列）。
- 旧 `/synonyms` 入口移除后，`auth` 无影响（沿用统一登录中间件）。

## 九、参数与开关

`app/params/registry.py` 的 PARAM_META 声明区新增一行（`app/config.py` 提供默认常量并默认开启）：

- key `search.lexicon_expand`，group `search`，默认 `1`（开）。
- 语义：0 = 检索关闭词条扩展（退回纯原词 FTS），1 = 开启。规则引擎归一化与易混淆提示**不受此开关影响**（它们与「是否扩展检索」无关）。
- 读取点仅 `sql_search` 的薄包装处；默认值回退、DB 覆盖、param_profiles 方案 apply 机制沿用现有参数体系，无需新增机制。
- 缓存：`_cache_key` 增补该值（见 7.1）；参数被改后现有检索缓存内结果在新值下由 key 维度自然隔离 + TTL 兜底。

## 十、测试策略（TDD）

### 单元（纯函数，无 DB）

- `tests/test_lexicon_expand.py`：
  - OOV 俗词「塌落度」经词条感知切词命中组、展开 OR（不依赖 jieba 能否切出）；
  - 规范词输入展开含俗称；多词组展开；普通词退化与 `build_match_query` 逐字节一致；
  - AND / OR（join_with）两种连接；空 variants 组不入；特殊字符/引号转义；无命中返回空串语义同原版；
  - **groups=[] 时输出 == 原 build_match_query**（回归护栏）。
- `tests/test_lexicon_normalize.py`：variants→canonical；canonical 不被反向替换；长词优先不破坏长词；confusable 不参与；禁用组数据不入。
- `tests/test_lexicon_confusable.py`：双命中产出、仅命中一个不产出、多组、空文本、distinguish 透传、启停。
- `tests/test_lexicon_store.py`：TTL/换库隔离；启禁过滤；变体拆分；异常回退空。

### 链路（临时库）

- `tests/test_hybrid_search.py` / `tests/test_search.py` 补：录入条文含「混凝土」、建 alias 组 `混凝土={砼}`，输入「砼」能召回该条文；输入「混凝土」也召回含「砼」条文（双向）；
  - 开关=0 时输入「砼」不扩展（行为与原版一致）；
  - **向量输入原文不变**：mock 或断言传给 `vector` 的关键词与原始 keyword 逐字节相同；
  - 词库增删后 `clear_search_cache` 生效（旧缓存不强留）。
- `tests/test_search_routes.py`：检索结果页 confusable 命中渲染提示条、未命中不渲染。
- `tests/test_qa_routes.py`：`question` 含 confusable 双词 → `confusable_hits` 非空且内容正确；单词 → 空。
- `tests/test_database.py`：lexicon 表建表 + 种子幂等；synonym_map 建表断言移除。
- `tests/test_lexicon_routes.py`（承接原 `test_synonym_routes`）：页面 200 / 需认证；CRUD、toggle、行内编辑、重复 canonical 幂等/报错、CSV 导入合法/非法/幂等报告、kind 字段分支校验。
- `tests/test_rule_engine.py`：注入结构改 lexicon 组后，现有归一化/匹配/父路径用例语义不变且绿。

### 收尾验收

- `grep -ri synonym_map app/ tests/ scripts/` 零命中；全量 `pytest` 绿。

## 十一、风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 检索扩展引入噪声召回（同义/别名词过宽） | 语义只放真等价词（箍筋类子类词不落 synonym）；全局开关可关；组词校验拒自反；QA 侧有阈值过滤兜底 |
| 查询端切词与索引 jieba 分词不一致导致漏召回 | 未命中片段退 jieba 原文切分 + 退化约束保一致性；OOV 由词条感知切词兜住 |
| `normalize_text` 短俗词误伤 | 沿用长词优先 + replace 局限注释；用具体术语单测锁定 |
| 词库变更后检索/规则读到旧数据 | 统一 `invalidate_lexicon_caches()` 唯一出口 + TTL 双保险 |
| 旧库升级后残留 synonym_map | `init_db` 幂等 `DROP TABLE IF EXISTS` |
| QA 复用同义扩展后召回变广 | 精排/阈值过滤已在链路内把关；开关可独立关检索扩展 |

## 十二、后续方向（本期不做，记录触发条件）

- **jieba userdict 联动**：把启用词条灌入 jieba 提升领域切词质量。触发条件 = 真实检索中出现「因 jieba 切碎领域词导致召回失败」的具体案例后单独立项（届时需处理存量 `search_text` 重建 + 全局词典热更新，见对话记录）。
- **LLM 主动消歧**：把易混淆命中作为上下文提示注入 prompt（需评估 token 与生成稳定性收益）。
- **AI 辅助建词 / 自动同义发现**：从 QA 日志与检索无命中查询中挖掘候选词条。
