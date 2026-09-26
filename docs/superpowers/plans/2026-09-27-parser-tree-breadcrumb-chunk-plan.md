<!-- /autoplan restore point: "C:\\Users\\lxl18\\.gstack\\projects\\DKLoong-construction-spec-query-v2\\main-autoplan-restore-20260927-011708.md" -->
## Implementation plan
# 设计：规范层级树 + 面包屑检索 + 超长条文子块

**日期**：2026-09-27　**状态**：已经 CEO 评审，待 eng 评审

> ## ⚠️ CEO 评审修正（2026-09-27，正文下方各部分以此为准）
>
> 本文档的下述原文已被评审**修正或证伪**，实现时不得按原文执行。完整依据与实测见文末
> 「CEO 评审记录」。
>
> | 原文位置 | 原文 | 修正后 |
> |---|---|---|
> | §4.2 | 只加 `section_path` 一列 | **必须再加 `clauses.breadcrumb`（预分词）**——触发器不能调 Python，无此列无源可拷；且该列 ALTER 须先于触发器（重）建 |
> | §4.3 收益③ | 「置 0 即完全关闭」 | **证伪**：实测 `bm25(f,1.0,0.0)` 下仅命中 breadcrumb 的行仍被返回。改为「权重=0 时发**列限定 MATCH**」方成立 |
> | §4.3 边界 | 面包屑"只通过加权列参与" | 保留，但须补：这是**召回**变更（非仅排序），且**改权重不能关闭召回**（除用列限定 MATCH） |
> | §4.4 | `clause_embeddings` 按 SQL 口吻「加列」 | 它是 **LanceDB 表**（三处 schema 逐字重复），**不能 ALTER**，需重建 schema；`search` 去重前须超取；`hybrid_search` 去重取**最优**而非最后 |
> | §4.4 连带 | 「health_check 覆盖率统计改为按 clause_id 去重」 | **误报**：实测 `health_check.py:66` 已是集合式 `{int(v) for v in ...}`，去重天然成立。真正要改的是它用 `to_arrow()` 读**全表含 embedding 列** |
> | §4.5 tooltip | 含「目次」 | **移除「目次」**（目次仍整段丢弃，从不入库） |
> | §5 | `build_search_text` 影响面 = 1 个测试文件 | **11 个测试文件约 45 处 + `scripts/probe_jieba_terms.py`**；`build_embed_text` 是 **4 个生产调用点**（漏 `maintenance_routes.py:109`、`vector_search.py:141`） |
> | §5 注 | 「bm25 若不支持绑定参数则用字面量」 | **伪风险，整段删除**——实测绑定参数可用 |
> | §6 用例 9 | 「置 0 等价于不参与」 | 改写为「权重=0 时仅命中 breadcrumb 的行**不返回**」 |
> | §6 指标 | 三项（结构召回/假阳性/超长条文数） | **增加两条**：① 正文总量**守恒**断言 ② 分类 dim 标签分布回归；批一另增「面包屑覆盖率」 |
> | §8 | Task 3 目次对齐立项 | **降为证据触发**（T17）：八个计划点名节点实测在正文中均可识别，真因是层级撞层（Task 1 职责） |
> | §8 | 一次交付 8 个 Task | **拆两批**：批一 = Task 0/1/2/6；批二 = Task 3/4/5 |
>
> **新增批二级门禁**：加面包屑后必须**全量重建向量**（实测 `sync_with_db` 只补缺失 id，不重嵌）。

## 1. 背景

导入管线把文档切成「条文」（clause）作为唯一检索单元。实测暴露三类问题：

**A. 层级骨架丢失，内容归属错位**（CJJ 2-2008 / JGJ 107-2016 实测）

```
                      CJJ2      JGJ107
源结构编号(去重)        850        84
库中条文号             754        73
召回                 744/850=87%  71/84=84%
  ├ 漏掉的「内节点」（章名/节名）  103       12
  └ 漏掉的「真叶节点」              3        1   （18.8.9 / 日期碎片2008 / 英文标题 ; 3.0.1）
臆造出「数字编号」                  0        0
库中「不含数字的条文号」           123        2   （主控项目54 / 一般项目59 / 英文标题…）
「主控项目/一般项目」吞走的字符       209,366 = 全规范正文 47%
```

三个连带缺陷：**同级父级被提前 pop**（`6.1.1` 丢掉"一般规定"）、**h1/h2 混用致跨章泄漏**（`7.0.1` 父链混进第 6 章标题）、**`level`/`parent_path` 根本没入库**（`parent_clause` 硬编码 `None`）。

**B. 目次骨架送上门却被丢弃**：`目次` 命中 `_NON_CLAUSE_FILTER_TITLES` 被整段丢弃；无「目次」二字时（CJJ2 该标题是图片），目录行又被 `_match_clause_line` 的 `[\.．]{5,}` 规则排除。实测 **CJJ2 目次含 135 个编号 ⊇ PDF 书签 133 个**（书签还漏了 `20.3`/`21.3`），且扫描件 OCR 同样能拿到目次。

**C. 超长条文尾部不进向量索引**（修正后的实测值）

```
模型 bge-small-zh-v1.5  max_seq_length=512
CJJ2 : 886 条真进检索池，其中 21 条(2%) 超限，超出 5,155 token
JGJ107:  71 条，          其中  4 条(5%) 超限，超出   374 token
典型：23.0.1(+1379, 73% 不可见) / 13.3.5 / 6.3.4 / B.0.2~B.0.4 / A.1.3
切成子块后行数：CJJ2 1009→1061 (+5%)，JGJ107 73→79 (+8%)
```

> 早期记录曾写「67~76% 正文永不进向量索引」，那是**字符数占比**且用了含标记的原始 `content`，属误报，已更正为上述数值。

## 2. 依据

全部判定规则见 `docs/standards/parser-判定规则与依据.md`（R1~R17，每条附条号原文）。关键三条：

- **R7 / 182 号第三十三条**：「当某节内容较多或内容较复杂时，可在该节增加**次分组单元**，但**所属节的条文编号应连续**；次分组单元的编号应采用**大写罗马数字**顺序编号」→ `Ⅰ 主控项目` 是次分组单元，**不是层级**
- **R3 / 第三十五条**：「当章内不分节时，条的编号中**对应节的编号应采用"0"表示**」→ `X.0.Y` 合法，父链**无** `X.0` 节点
- **R14 / GB/T 1.1—2020 §7.3.3**：「某一章或条中，其下一个层次上的各条，**有无标题应一致**」

## 3. 决策记录（均已确认）

| 项 | 决策 |
|---|---|
| 树的存储 | **C**：`clauses` 加一列 `section_path` 存面包屑快照；内节点不建行 |
| 内节点是否入检索池 | **不入**（仅作面包屑文本注入条文索引） |
| 内节点判据 | **「有无自身正文」**（非"是章/节还是条"） |
| 超长条文子块 | **本轮做**（复用向量表 + `chunk_index`，检索去重） |
| ANN 索引 | **不做**（59 份规范 7,105 页 → 约 2.4 万条，暴力扫描足够） |
| 标题/正文判据 | **本轮换成兄弟一致性**（替换 `_looks_like_title` 的 ≤20 字） |
| 非条文块 | **打标保留**（`clause_is_non=1`）；名单按法定名称扩充 |
| 编写规则适用 | **以 182 号为主，GB/T 1.1 作补充** |
| LLM 参与切分 / `一、二、` / `第X条` | **不做** |
| 存量数据 | 重导无顾虑（测试阶段常态） |

## 4. 设计

### 4.1 解析器改造（`app/parser/md_parser.py`）

**① 层级只由编号点数推导**，不再用 `#` 数量。修掉"两把尺子"与 Paddle `#` 不稳定导致的跨章泄漏。

**② `X.0.Y` 按 R3 处理**：段内为 `0` 的那一段**不产生节点**。`3.0.1` 的父链 = [`3 章`]。

**③ 内节点判据 = 「有无自身正文」**

| 情况 | 处置 |
|---|---|
| 有编号 + **有**自身正文 | **叶条文**：建行、可检索；`section_path` = 祖先内节点标题链 |
| 有编号 + **无**自身正文 | **内节点**：不建行，仅作为后代 `section_path` 的一段 |

> 这条同时解决**附录**：`附录A`（CJJ2 是一张 35K 字符验收记录表）**没有下级条号也无自身条号**，若按"章/节一律内节点"会把整张表丢掉。按本判据它是**叶条文**，正常入库可检索 —— 符合 R11（附录与正文同等效力）。

**④ 标题/正文一致性用兄弟多数表决（R14）**：两遍解析 —— 第一遍收集编号行并按 **(层级, 父节点)** 分组投票，第二遍按各组多数结论决定每行是"带标题条"还是"无标题条"。
→ 修掉 `3.0.1 接头设计应满足强度及变形性能的要求`（16 字无句号被判标题型后丢失）：同层 `3.0.2~3.0.9` 皆为无标题条 → 多数表决判它也**无标题** → 正文归其自身 → **不丢**。

**⑤ `section_path` 栈式构建**：修掉"同级父级被提前 pop"与跨章泄漏。

**⑥ 目次优先（TOC-first）**
1. 提取目录行 —— **行形状判定**（编号 + 点线/省略号 + 页码），不依赖"目次"二字
2. 得到**预期骨架**（章/节编号集合；R9 语法、R9b 页码规律）
3. 与正文推断结果**对齐**：补回丢失的内节点（`18.3~18.7`、`20.3`）、发现不一致并记录
4. 目次不可得 → 回退纯推断（**不改变现有行为**）

**⑦ 三个小修复**
- `#` 路径不检查中文 → 英文标题成条文（`_match_clause_line` 有 `re.search(r'[一-鿿]')`，`#` 路径漏）
- `_extract_clause_no` 兜底 `return title` → 标题变条文号（改：匹配不到编号即不当条文）
- 裸露 4 位年份（`2008`）成条文号

**⑧ 非条文块名单按法定名称扩充（R8/R8b）**

| 法定名称（182 号第六、七条） | 现状 | 改动 |
|---|---|---|
| 公告 | ❌ 未覆盖（spec20 有 2 条伪条文） | 新增 |
| 引用标准名录 | ❌ 未覆盖（列表项被误吞成 `clause_no='1'/'10'`） | 新增 |
| 标准用词说明 | ⚠️ 只覆盖 `本规程用词说明`；CJJ2 的 `本规范用词说明` 漏判 | **改为按法定名称匹配**，再兼容变体 |

三者一律 `clause_is_non=1`（打标保留）。

### 4.2 存储（`app/database.py`）

`clauses` 表新增一列：

```sql
section_path TEXT   -- 面包屑快照，如 "6 接头的现场加工与安装 > 6.3 接头安装"
```

迁移方式沿用现有 `_migrate_*` 模式（`ALTER TABLE ... ADD COLUMN`，幂等，已有先例见 `clause_is_non`）。

### 4.3 检索注入 + FTS 两列改造 + `breadcrumb_weight`

**当前限制**：`clauses_fts` 是**单列** `fts5(search_text)`，排序用 `bm25(clauses_fts)` —— 单列**无法给"面包屑那部分"单独加权**。

**改法**：FTS 改**两列** `fts5(search_text, breadcrumb)`，排序用

```sql
bm25(clauses_fts, 1.0, ?)   -- 第二列权重 = breadcrumb_weight
```

**两列的内容划分（关键，避免双重计数）**：

| 列 | 内容 | 权重 |
|---|---|---|
| `search_text` | `jieba(title + content)` + `clause_no` —— **不含面包屑** | 固定 1.0 |
| `breadcrumb` | `jieba(section_path)`，如 `6 接头的现场加工与安装 6.3 接头安装` | `breadcrumb_weight` |

> 面包屑**只**通过加权列参与，**不再**同时拼进 `search_text`。否则它会以权重 1.0（埋在正文里）与 `breadcrumb_weight` 各计一次，权重语义失真、且 `breadcrumb_weight=0` 也无法真正关掉它。

**收益**：
1. **改权重无需重建索引**（权重在**查询时**生效）
2. **避免 BM25 长度归一化惩罚** —— 面包屑独立成列，不会把 `search_text` 撑长而导致正文被稀释
3. 面包屑可**单独**参与匹配：用户搜"接头安装"时，命中 breadcrumb 列的该节条文获得加权分；置 0 即完全关闭

**参数**（`app/params/registry.py`，`search` 分组）：

```python
_num("search.breadcrumb_weight", "search", "面包屑权重",
     0.3, 0.0, 2.0, "0~2",
     "面包屑（章/节路径）在 BM25 排序中的列权重；0=不参与，越大越偏向命中节名的条文。")
```

**明确边界**：`breadcrumb_weight` **只作用于 FTS/BM25 侧**。向量侧的面包屑是**烘焙进 embed_text** 的，调它需**重建向量**（维护页有按钮）。这是刻意的取舍——换取"调参不重建 FTS"。

**函数签名扩展**（两个函数、3 个调用点）：

- `build_search_text(clause_no, title, content, section_path="")` → 返回 **`(search_text, breadcrumb)` 两列**（`search_text` **不含**面包屑，见上表）
- `build_embed_text(code, spec_title, clause_no, clause_title, content, section_path="")` → 面包屑拼在**条文号之后、正文之前**（保留位置信号）。向量侧**无法分列加权**，故其唯一调整手段是"重建向量"

调用点：
- `app/database.py` `_migrate_search_text` —— **启动时全量回填**，故 FTS 侧不需手工重建
- `app/routes/import_routes.py`（导入写入）
- `app/routes/spec_routes.py`（条文编辑重索引）

### 4.4 超长条文子块

- **切块**：对 `plain_text(content)` 按 **512 token** 切；**仅超限条文切**，短条文 `chunk_index=0` 一行
- **写入**：`clause_embeddings` 加 `chunk_index` 列（INTEGER，默认 0），同一 `clause_id` 多行
- **检索去重**：向量臂取回候选后**按 `clause_id` 保留最高分那行**（同时提升结果多样性——防止一条条文占满榜单）
- **连带修改**：「行数 = 条文数」假设失效 →
  - `app/maintenance/health_check.py`：向量覆盖率统计改为**按 `clause_id` 去重后**计算
  - 维护页展示同步

### 4.5 检索侧过滤（用户第 1 条要求）

- 非条文块一律 `clause_is_non=1` → **自动纳入现有过滤**（`sql_search.py:30`、`hybrid_search.py:120` 已按此列过滤）
- **复选框文案**：「包含前言·条文说明」→ **精简名**（建议「包含非条文内容」）
- **tooltip**：用 `title` 属性说明具体包含范围（前言 / 目次 / 条文说明 / 用词说明 / 公告 / 引用标准名录），保持界面整洁

### 4.6 详情弹窗

`app/templates/partials/clause_detail.html:55` 的「来源：{规范名}」后拼上 `section_path`。

## 5. FTS 列数变更的影响面（必须同步修改）

| 文件 | 位置 | 改动 |
|---|---|---|
| `app/database.py` | `:55`（SCHEMA_SQL 建表） | 两列 |
| `app/database.py` | `:190/:195/:199/:200`（3 个触发器） | 写两列 |
| `app/database.py` | `:249-253`（旧表 drop/重建） | 增加"单列 → 两列"的迁移判定 |
| `app/database.py` | `:266-268`（backfill） | 回填两列 |
| `app/search/sql_search.py` | `:96`（`bm25`） | `bm25(clauses_fts, 1.0, ?)` |
| `app/maintenance/health_check.py` | `:102/:311-313`（fts_mismatch 修复） | 写两列 |
| `app/main.py` `:60` / `app/routes/maintenance_routes.py` `:221` | optimize | 无需改（无列名） |
| `scripts/migrate_fts.py` | 存量迁移脚本 | 同步 |
| `tests/test_database.py` | `:99~216`（含"旧结构迁移"用例） | 该用例正是迁移测试，需扩展为单列→两列 |

> **实现时验证**：`bm25()` 的权重参数能否用**绑定参数**。若不支持，改用 `float()` 强转后的字面量并在代码注释说明（`breadcrumb_weight` 有 `vmin/vmax` 校验，非注入面）。

## 6. 测试与验收

**Task 0 的「目次提取 + 骨架对齐」原型**同时是**回归门禁**：

- 改完 parser 重跑结构勘察，**三项指标不得退化**：结构召回 / 假阳性 / 超长条文数
- 夹具来源：14 份 A 类书签真值 + CJJ2/JGJ107 目次骨架 + 5 份复杂样本 + 两份编写规则原文
- 逐 Task 走 TDD（先写失败测试）→ 增量测试 → `pyright` 0 error → 提交

**关键回归用例**（每条对应本设计的一个判据）：
1. `3.0.1` 单元格不被丢弃（R14 兄弟一致性）
2. `6.1.1` 的 `section_path` 含"一般规定"（修 pop-before-parent）
3. `7.0.1` 的 `section_path` **不含**第 6 章标题（修跨章泄漏）
4. `18.3~18.7`、`20.3` 由目次骨架补回
5. `Ⅰ 主控项目` 成为**属性**而非层级；123 条伪条文号消失
6. `附录A`（有自身正文）**作为叶条文可检索**（不被当空壳内节点丢掉）
7. `公告` / `引用标准名录` → `clause_is_non=1`
8. `CJJ2 的「本规范用词说明」`被正确打标（法定名称匹配）
9. `breadcrumb_weight` 改值后**无需重建**即影响排名；置 0 等价于不参与
10. 超长条文切块后：行数 +5~8%、检索按 `clause_id` 去重、覆盖全部条文不丢不重

## 7. 明确不做

ANN 索引（2.4 万条暴力扫描足够）· LLM 参与切分 · `一、二、` 中文顿号 · `第X条` 句式 · 「节」作为独立可返回实体（树选 C，将来需要再加表重导）

## 8. 交付顺序

1. **Task 0** 结构勘察 + 目次提取原型（产出回归夹具）
2. **Task 1** 解析器：层级/`X.0.Y`/内节点判据/`section_path`（含 3 个小修复）
3. **Task 2** 标题一致性（R14 兄弟表决）
4. **Task 3** 目次对齐
5. **Task 4** 存储 + 检索注入（`section_path` + FTS 两列 + `breadcrumb_weight`）
6. **Task 5** 超长条文子块
7. **Task 6** 非条文块名单 + 检索侧复选框文案/tooltip + 详情弹窗
8. **Task 7** 重导 CJJ2（及全部语料）验证

> 依赖：Task 1 是 2/3 的基础；Task 4 依赖 1（需要 `section_path` 有值）；Task 5 独立。
---

# CEO 评审记录（/plan-ceo-review，2026-09-27）

**Mode: HOLD SCOPE**（保范围 + 最大严谨度）。外部复核：Codex 已完成（`outside_status: completed`）。

## 评审决议（8 项，全部已答）

| ID | 议题 | 决议 | 来源 |
|---|---|---|---|
| D2 | Task 5 超长条文子块去留 | **保留在范围内**（维持原裁定） | 用户 |
| D3 / D3.2 | 面包屑参与检索的方式 | **要召回 → FTS 两列**（附语义修正，见下） | 用户 |
| D4 | 交付顺序 | **拆两批**：批一 = Task 0/1/2/6；批二 = Task 3/4/5 | 用户 |
| D6 / D6.2 | Task 3 目次对齐 | **降为证据触发**：批一用「面包屑覆盖率」量残余缺口，再决定是否做 | 用户 |
| — | 目次进不进库 | 不进库（维持整段丢弃），tooltip 移除「目次」 | 事实更正，无需决策 |
| — | Task 4 参数语义 | 权重=0 改发**列限定 MATCH**，使「0=不参与」为真 | 随 D3.2 |

## 实测证伪的三条计划断言（本次评审最硬的产出）

**① `breadcrumb_weight=0` 不能关闭 breadcrumb 列的召回。**
本机 sqlite 3.50.4 实测：`fts5(search_text, breadcrumb)` 下，仅命中 breadcrumb 的行在 `bm25(f,1.0,0.0)` / `0.3` 下**仍被返回**（分数 `-0.0`）。FTS5 的 `MATCH` 与列无关，权重只缩放评分、不改变召回。
→ 计划 §4.3「收益③：置 0 即完全关闭」与 §6 用例 9 断言了错误行为。
→ **正解**：权重为 0 时改发列限定 MATCH（`MATCH 'search_text:关键词'`），使「0=不参与」成立。

**② `bm25()` 的权重参数可以用绑定参数**（`bm25(f,1.0,?)` 实测正常）。
→ 计划 §5 注中「若不支持则用 `float()` 字面量」是**伪风险**，可整段删除。删除后，Section 3 所记的**唯一一处注入面随之消失**。

**③ Task 3 的立项理由（「目次补回 18.3~18.7、20.3」）不成立。**
用**当前未修改**的解析器解析 CJJ2 源 md（`data/outputs/f543577f/f543577f.md`）实测：

| 计划点名需目次补回 | 正文里的可识别标题行 |
|---|---|
| 18.3 | `### 18.3 索 塔` ✅ |
| 18.4 | `### 18.4 施工猫道` ✅ |
| 18.5 | `### 18.5 主编架设与防护` ✅ |
| 18.6 | `### 18.6 索鞍、索夹与吊索` ✅ |
| 18.7 | `### 18.7 加 劲 梁` ✅ |
| 18.8 | `### 18.8 检验标准` ✅ |
| 20.3 | `### 20.3 桥面铺装层` ✅ |
| 21.3 | `### 21.3 桥头搭板` ✅ |

八个全部在正文中可识别，无需目次。它们进不了父链的真因是**层级用了两把尺子**（`###` 数量 vs 编号点数）→ `18.3`（level 3）与 `18.3.1`（点数 2 → level 3）**撞层** → 被 `>= level` 提前 pop。这是 **Task 1 ④⑤** 的职责。
**边界**：仅实测 CJJ2 一份 + 计划点名的编号，**不能推出**「所有规范的缺失节点都能从正文恢复」（CJJ2 的「目次」二字本身就是图片，说明扫描件标题确可能丢）。故 Task 3 降为证据触发而非删除。

## 本次评审新发现的缺陷（计划原未提及）

**🔴 CRITICAL-1 缺守恒断言。** 计划称 123 条伪条文号「消失」、`Ⅰ 主控项目` 变属性，但**没有一句描述那 209,366 字符（47% 正文）重新分配到哪**，§6 也无对应断言。风险形态：伪条文号消失 + 内容一并丢弃 = 47% 语料静默蒸发，而 §6 十条用例全绿。
→ **必须补守恒断言**（重导后正文总量不减少），这是唯一能拦住它的测试。

**🔴 CRITICAL-2 分类引擎是被隐藏的耦合，计划零提及。**（我与 Codex 各自独立发现，属跨模型一致）
`import_routes.py:469` → `classify_clause(cd["content"], cd.get("parent_path", []), rules)` → `rule_engine.py:66` 把祖先标题拼进 `augmented_text`。**`parent_path` 是分类规则的匹配文本的一部分。** 栈式重建会改变它（`6.1.1` 多出「一般规定」、`7.0.1` 丢掉第 6 章标题）→ dim 得分与标签随之改变。
→ 且实测已证实 `parent_path` **今天就是被污染的**：`5.1.7` 的父链段是 `'钢模板的面板变形值为1.5mm;'`、`18.3.1` 的是 `'合龙段的长度宜为 2m'`——正文本句被 `_looks_like_title` 提成标题后进了分类输入。
→ 需补：重导前后 **dim 标签分布回归基线**。

**🔴 CRITICAL-3 FTS `breadcrumb` 列没有来源列。** FTS 两列要求 `breadcrumb` 列内容是 `jieba(section_path)`，但触发器只能从 `clauses` 的列拷贝，而 §4.2 只加了 `section_path`（原始文本）。
→ 必须**另加一列 `clauses.breadcrumb`**（应用层写入分词结果，与 `search_text` 同构），自带自己的 ALTER + backfill。本仓 schema 注释已写明「SQLite 触发器不能调 Python，故 jieba 分词结果由应用层写入」——计划让触发器调 jieba，与该既有约束矛盾。
→ 实测佐证：未分词的整段中文在默认分词器下折叠为单 token，查 `接头安装` 命中、查 `接头` 与 `安装` **0 命中**；jieba 分词后反之。**不做预分词列，功能几乎无效。**
→ **迁移顺序约束**：该列 ALTER 必须先于触发器（重）建，否则 `CREATE TRIGGER` 引用不存在列而失败。

**🔴 CRITICAL-4 影响面严重低估（Codex 发现，我已逐条核实并发现实际更严重）。**

| 函数 | 计划所列 | 实测 |
|---|---|---|
| `build_search_text` | 1 个测试文件 | **3 生产调用点 + 9 个测试文件 47 处 + `scripts/probe_jieba_terms.py` 7 处（`:57` 还复刻了实现）≈ 54 处**；`tests/test_tokenize.py` 含 13 处直接断言（其中 `== " "` 断言返回类型） |
| `build_embed_text` | 2 个调用点 | **4 个生产调用点**，漏 `maintenance_routes.py:109` 与 `vector_search.py:141`；`tests/test_embed_text.py` 有 6 处精确字符串断言 |

> **数字更正（2026-09-27 复核）**：本节初稿写「11 个测试文件约 45 处」，实测为
> **9 个测试文件 47 处 + 脚本 7 处**。且 Codex 点名的 `tests/test_classify_param_reading.py`
> **一处都不调用** `build_search_text`；`tests/test_text_clean.py` 仅在其 docstring 中提及
> （无调用点，不需改）。准确清单见批二计划 Task 3 Step 4 的实测基线表。

**🔴 CRITICAL-5 加面包屑后必须全量重建向量，计划未设门禁。**
`vector_search.sync_with_db:137` 实测为 `missing = [r for r in all_rows if r["id"] not in vector_ids]`——**只补缺失 id，不重嵌已有条文**。新参数有默认值 `""`，故漏掉的 4 个调用点会**静默**产出不含面包屑的向量。
→ 本仓已有 `tests/test_reindex_vectors_script.py`，其 docstring 明写「避免再次漂移」——**此坑已踩过一次**。
→ 需设门禁：批二完成后必须全量重建向量。

**🔴 CRITICAL-6 LanceDB 侧影响面整体缺失。** 计划 §5 的表只覆盖 FTS。实际需改：`vector_search.py:40-45`、`vector_search.py:~205-215`、`import_routes.py:551-558` **三份逐字相同**的 `pa.schema([...])`（建议抽 `_embedding_schema(dim)` 工厂），另加 `spec_routes.py:242`、`maintenance_routes._run_rebuild`、`vector_search.index_clause/batch_index/search`。
→ 且 **LanceDB 不能像 SQLite 那样 ALTER**，现存表无 `chunk_index`，必须重建 schema。
→ `search(limit=top_k)` 需在按 `clause_id` 去重前**超取**，否则去重后有效召回缩水。
→ `hybrid_search` 的 `dist_map[id]=dist` 是「后者覆盖」，会保留**最差**的 chunk 距离——去重必须取最优而非最后。

**⚠ WARNING-7 131 个条文号重复、涉及 388 行（占解析结果 38%），其中仅 2 行 `is_non_clause=1`。**
样例：`'1.0.2'` x2、`'2.0.1'` x2、`'4'` x3、标题被当条文号 x2。部分重复可能合法（正文与条文说明重号），但**实测仅 2 行为 is_non**，与「条文说明应被打标」不符。
→ 交互点：**R14 兄弟表决按 `(层级, 父节点)` 分组，而重复条文号使「兄弟组」定义失效**（同一父键下混入两个不同文档位置的条文）。需在批一验收中查明。

**⚠ WARNING-8 重导前必须排除 `data/uploads/`。** 该目录已累积 147 个 `# 测试规范` 夹具 md（pytest 污染产物）。「重导全部语料」若扫描它，会把夹具当规范导入并让守恒断言失真。

**⚠ WARNING-9 测试隔离需覆盖三处路径。** 本仓 `app/database.py` 直接读模块级常量、无环境变量入口（历史 learning 9/10 已记录），测试靠 `monkeypatch.setattr("app.database.DATABASE_PATH", ...)`。**LanceDB 路径与 `data/uploads/`/`outputs` 同样没有隔离**——这正是历史两次污染事故的成因。新增的重导相关测试必须同时 patch 三处。

**⚠ WARNING-10 `_vector_ids_and_state` 读全表含 embedding 列。** `health_check.py:66` 用 `tbl.to_arrow()` 仅为收集 id 集合。1088 行 ≈ 2 MB 尚可；计划自己的规模估计（全语料约 2.4 万条）≈ **49 MB/次**，且维护页每次打开都跑。子块再 +5~8%。→ 改为只 select `clause_id` 列。
（同一函数的 `except Exception:` 裸捕获且不记日志，违全局规则 1.2，属既有债。）

**⚠ WARNING-11 子块后需重导后 compaction。** 历史记录真实表曾 `rows=73 / version=173`。子块使每次重导写入 +5~8%。计划未提 `optimize`/`cleanup`，否则版本数持续累积、查询延迟随版本线性变差。

**⚠ WARNING-12 可访问性与展示形态未定。** tooltip 用 `title` 属性 → 键盘 focus 与触屏**均不显示**。空 `section_path` 会拼出「来源：X > 」尾随分隔符。面包屑是否是可点击层级链接未定（§7 已明确「节不作为独立可返回实体」→ 应为纯文本，需显式写入）。

**⚠ WARNING-13 `parent_clause` 将永久为 NULL** → `health_check` 的 `orphan_parent` 检查沦为死代码；`models.Clause` 也未同步。

**⚠ WARNING-14 计划 §6 的三项回归指标不含「面包屑覆盖率」。** 批一若只按这三项验收，可能出现「三项全绿而按节名召回对若干节整体失效」——正是零静默失败要拦的形态。**批一必须增加该指标**（含「缺的是哪些节」）。

## NOT in scope

| 项 | 实际答案 | 理由 |
|---|---|---|
| ANN 索引 | 不做（原裁定维持） | 全语料约 2.4 万条，暴力扫描足够 |
| LLM 参与切分 / `一、二、` / `第X条` | 不做（原裁定维持） | 实测规范里 0 命中 |
| 「节」作为独立可返回实体 | 不做（原裁定维持） | 选 C 方案换取免迁移 |
| 目次入库为可检索内容 | 不做 | 目次在本次设计中是骨架来源（读取），非内容；入库需另定义 135 行的 clause_no/section_path/展示 |
| 重导期间的检索可用性保障（不变量化 + 写锁） | **未决** | 属既有行为，本计划未使其变差；记为待议 |
| 参数越权写入防护核对 | **声明未验证** | `app/routes/params_routes.py` 不存在（实为 `param_routes.py`），我未核实其鉴权装饰器。`implementation owner must prove` |

## What already exists（复用情况）

| 既有能力 | 位置 | 本计划是否复用 |
|---|---|---|
| 解析器已产出 `level` / `parent_path` | `md_parser.py:207-208,230,267` | ✅ 修既有逻辑而非重建 |
| 幂等 `ALTER TABLE` 迁移模式 | `database.py` `_migrate_search_text` / `_migrate_rule_pending_clause_nullable` | ✅ |
| FTS 存量迁移脚本 | `scripts/migrate_fts.py` | ✅ 承载存量迁移 |
| `plain_text()` 文本清洗 | `app/ai/text_clean.py:92` | ✅ 子块切分前调用 |
| 导入分批写入向量 | `import_routes.py:565` `VECTOR_WRITE_BATCH` + `tests/test_import_vector_batch.py` | ✅ 子块写入沿用 |
| 向量覆盖率统计（集合式、已天然去重） | `health_check.py:66` `{int(v) for v in ...}` | ⚠ **计划称需改成「按 clause_id 去重」——实测该实现本就是集合，去重天然成立**。该项为**误报**，只需修 `to_arrow()` 全列读 |
| 检索侧 `clause_is_non` 过滤 | `sql_search.py:30`、`hybrid_search.py:120` | ✅ 新增非条文块自动纳入 |
| 维护宫格红/黄分级 | commit `2b6bb78` | ✅ 新增块自动进告警面 |

## Dream state delta

```
  CURRENT                      THIS PLAN                       12-MONTH IDEAL
  条文平铺、层级丢失    --->   面包屑快照 + FTS 加权召回   --->  层级可导航：章节树、
  47% 正文挂伪条文号            + 超长条文子块                  按章/节筛选与聚合、
  章节级查询失效                （拆两批交付）                  引用关系图谱
```
本计划走到「检索侧可感」，未到「层级可导航」（§7 明确不做）。差距是刻意的取舍（选 C 免迁移），**建议将来需要时加表重导**，而非从快照反解。

## Error & Rescue Registry

| 方法/代码路径 | 会出什么错 | 异常类 | 已救? | 救护动作 | 用户看到 |
|---|---|---|---|---|---|
| `parse_markdown` 两遍解析 | 兄弟组为空（单条成组）无法多数表决 | 无 | **N ← GAP** | 未定义 | 单条退化为原判据（行为未定义） |
| `parse_markdown` | 目次补齐引入不存在的内节点 | 无 | **N ← GAP** | 未定义 | 面包屑静默污染；Task 3 降级后风险消除 |
| `parse_markdown` | `section_path` 为 `""` | 无 | **N ← GAP** | 未定义 | 弹窗拼出「X > 」；FTS 空列行为未验证 |
| `clauses` 触发器写两列 | `breadcrumb` 列缺失（计划漏列） | `sqlite3.OperationalError` | **N ← GAP** | — | 导入整体失败（会炸，可接受） |
| `_migrate_search_text` | DROP 后重建中途失败 | `sqlite3.OperationalError` | **N ← GAP** | 未定义 | 服务启动但检索全空 |
| LanceDB 加 `chunk_index` | 三处 schema 漏改一处 | `pa.ArrowInvalid` / 静默错配 | **N ← GAP** | — | 子块写不进或错配 |
| LanceDB 加列 | 老行 `chunk_index` 取值未定义 | 无 | **N ← GAP** | — | 去重行为未定义（静默） |
| 子块切分 | content 超长无标点 → 切点不落边界 | 无 | **N ← GAP** | 未定义 | 子块语义破碎 |
| 子块去重 | `chunk_index` 为 null 时排序未定义 | 无 | **N ← GAP** | — | 保留的可能是最差 chunk |
| `classify_clause`（受牵连） | `parent_path` 变化 → 得分/标签变化 | 无 | **N ← GAP** | — | 分类标签静默改变 |
| 重导流程 | 中途失败 → 规范半成品 | — | 部分（`rollback` 见 `:345/:605`） | 待验范围 | 半成品数据 |
| `bm25` 绑定参数 | 不支持 | — | ✅ 实测支持 | 删除伪风险 | 无感 |
| 目次不可得 | 提取失败 | — | ✅ | 回退纯推断 | 无感 |
| `_vector_ids_and_state` | 读表失败 | `except Exception`（过宽） | ⚠ 已救但无日志 | 标 "error" | 维护页「待重建」，原因不入日志 |

## Failure Modes Registry

```
  CODEPATH                | FAILURE MODE              | RESCUED? | TEST? | USER SEES?  | LOGGED?
  ------------------------|---------------------------|----------|-------|-------------|--------
  伪条文号消失+内容丢弃     | 47% 正文静默蒸发           | N        | N     | Silent      | N   ← CRITICAL
  分类引擎 parent_path 变   | dim 标签漂移              | N        | N     | Silent      | N   ← CRITICAL
  FTS breadcrumb 空         | 该条文整条不进 FTS 索引     | N        | N     | Silent      | N   ← CRITICAL
  LanceDB 三处 schema 漏改  | 子块写入错配               | N        | N     | Silent      | N   ← CRITICAL
  LanceDB 老行默认值未定义  | 去重取错 chunk             | N        | N     | Silent      | N   ← CRITICAL
  加面包屑后未重建向量      | 旧向量与文本永久不一致      | N        | N     | 检索质量降   | N   ← CRITICAL
  sync_with_db 只补缺失     | 同一后果（既有机制）        | N        | N     | Silent      | N   ← CRITICAL
  母子块重复条文号          | R14 兄弟组定义失效         | N        | N     | Silent      | N   ← CRITICAL
  目次不可得                | 退回纯推断                | Y        | Y     | 无感        | Y
  bm25 绑定参数             | —（实测支持）             | Y        | —     | 无感        | N
  向量写失败                | 条文无向量                | Y        | Y     | 宫格红点     | Y（WARN）
  重导中途失败              | 半成品数据                | 部分     | N     | 检索降质     | 部分
```
**CRITICAL GAP 计 8 条。** 全部为静默路径。

## Diagrams

**1. 数据流（含影子路径）**
```
INPUT 源md/OCR
  → VALIDATION md非空/非封面/非目次段
       ├ nil:   md 空 → [] ✓
       ├ 全是目次: → 零条文，**无告警** ← GAP
       └ 超长:  1009 条 + 向量
  → TRANSFORM 两遍解析 → 内节点判据 → section_path → 【目次对齐已降级】
       ├ 兄弟组空: 行为未定义 ← GAP
       └ 不一致:   记录到哪未定 ← GAP（Task 3 降级后暂不触发）
  → PERSIST SQLite INSERT + 触发器写 FTS 两列 / LanceDB 分批 add
       ├ 空 breadcrumb: FTS 空列行为未验证 ← GAP
       ├ 三处 schema 漏改: 静默错配 ← GAP
       └ 双写非原子: 中途失败 = 半成品 ← 部分救
  → OUTPUT 检索/问答
```

**2. 状态机（重导期间）**
```
  稳定(旧数据) ──启动──▶ 迁移完成(clauses.section_path+breadcrumb, fts 两列)
                            │
                            ├──重导开始──▶ FTS 已填 / 向量未填  ← 半成品①
                            │              「检索命中但向量臂空」
                            └──向量写入──▶ 稳定(新数据)
  半成品① 用户检索：静默降质、**无提示、无禁用** ← 未定义状态（既有行为）
```

**3. 部署顺序**
```
git 改代码 → ALTER clauses(section_path) → ALTER clauses(breadcrumb)   ← 必须先于触发器
           → DROP/重建 clauses_fts 两列 → 触发器重建 → backfill 两列
           → LanceDB 重建 schema(含 chunk_index) → 重导 → 全量重建向量
           → 重导后 optimize/cleanup → 冒烟（守恒断言 + 覆盖率 + 分类分布）
```

**4. 回滚**
```
SQLite 加列        → 不读即回滚，成本 ~0        ✅
FTS 两列           → 重建为单列，成本一次启动    ✅
LanceDB chunk_index → 删表重建向量，分钟~小时    ❌ 不可廉价回滚
模板               → git revert + ?v=N 递增     ✅
```
**可逆性：3/5**（测试阶段重导无顾虑拉高评分；但 LanceDB 加列不可廉价回滚、FTS 重建有退化窗口）

**5. 错误流**
```
任一静默路径触发 ──▶ 无异常、无日志、无用例失败
                      └─▶ 守恒断言（新增）是唯一兜底 ──▶ 不通过则批一/批二失败
```

## Stale Diagram Audit

本计划触及的文件内的 ASCII 图：
- `app/database.py` — FTS5 建表处的注释说明「独立表，非 external content 表」：**加第二列后该注释需更新**（仍成立但需补 breadcrumb 列的说明）
- `app/search/vector_search.py` — 三处 schema 定义无图；建议抽工厂时补一处「schema 唯一定义」的说明
- `docs/standards/parser-判定规则与依据.md` — R1~R17 表格**不含 `section_path` 的格式规范**（分隔符、编号是否入内、`X.0` 是否出现）。1 年后新人必踩，需补一节。

## Implementation Tasks

- [ ] **T1 (P1, human: ~1d / CC: ~30min)** — `app/parser/` — 层级只由编号点数推导 + `X.0.Y` 不产生节点 + 内节点判据「有无自身正文」+ `section_path` 栈式构建（修 pop-before-parent 与跨章泄漏）+ 三个小修复
  - Surfaced by: Section 1（层级两把尺子致 `18.3`/`18.3.1` 撞层，实测 `parent_path=['合龙段的长度宜为 2m','悬索桥']`）
  - Files: `app/parser/md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -v`；勘察脚本三项指标不退化
- [ ] **T2 (P1, human: ~4h / CC: ~20min)** — `app/parser/` — R14 兄弟多数表决（两遍解析）；**必须先解决 `_looks_like_title` 双调用点问题**（`_match_clause_line:126` 是行内纯函数，无权做跨行判断）
  - Surfaced by: Section 5(b)；计划只写「两遍解析」，未说明第一遍如何避免提前丢弃候选行
  - Files: `app/parser/md_parser.py`
  - Verify: 用例 `3.0.1` 不被丢弃；单条成组时的退化行为有明确定义与用例
- [ ] **T3 (P1, human: ~2h / CC: ~10min)** — 测试 — **守恒断言**：重导后正文总量不减少（先经 `plain_text()` 归一，避免标记残留导致自然下降）
  - Surfaced by: Section 2/6 CRITICAL-1（47% 字符无归属设计、无断言）
  - Files: `tests/test_import_reparse_conservation.py`（新建）
  - Verify: 该断言在「伪条文号消失但内容也丢」的变异下**必须失败**
- [ ] **T4 (P1, human: ~3h / CC: ~15min)** — 测试 — **分类标签分布回归基线**（重导前后按 dim 对比）
  - Surfaced by: Section 1/6 CRITICAL-2；`import_routes.py:469`→`rule_engine.py:66` 实测
  - Files: `tests/test_classify_baseline.py`（新建）
  - Verify: 人为改动 `parent_path` 时该测试必须报差异
- [ ] **T5 (P1, human: ~2h / CC: ~10min)** — `scripts/` — 批一新增**面包屑覆盖率指标**：完整多级 `section_path` 的条文占比 + 缺的是哪些节（用于量 Task 3 的残余缺口）
  - Surfaced by: Section 6/8 WARNING-14；D6.2 决议
  - Files: `scripts/survey_structure.py`（新建，即 Task 0 产物）
  - Verify: 对 CJJ2 输出覆盖率与缺失节清单
- [ ] **T6 (P1, human: ~3h / CC: ~15min)** — 仓库卫生 — 查明 **131 个重复条文号 / 388 行**：哪些是条文说明重号（应 `is_non_clause=1`）、哪些是真缺陷；并确认 R14 兄弟组定义在重号下如何成立
  - Surfaced by: Section 4 WARNING-7（实测仅 2 行 is_non）
  - Files: `scripts/`（诊断）+ 视结果修 `app/parser/md_parser.py`
  - Verify: 诊断脚本给出可解释的分类计数
- [ ] **T7 (P2, human: ~4h / CC: ~20min)** — `app/database.py` — 新增 `clauses.breadcrumb`（预分词）列；**ALTER 必须先于触发器（重）建**；`_migrate_search_text` 改两列 + 回填两列
  - Surfaced by: Section 1 CRITICAL-3（触发器不能调 Python，无源可拷）
  - Files: `app/database.py`、`scripts/migrate_fts.py`
  - Verify: `pytest tests/test_database.py -v`（单列→两列迁移用例扩展）
- [ ] **T8 (P2, human: ~4h / CC: ~20min)** — `app/search/` — `bm25(clauses_fts, 1.0, ?)` **绑定参数**（删除字面量回退）；权重为 0 时改发**列限定 MATCH**
  - Surfaced by: Section 3 + 实测①②（绑定参数可用；权重置 0 不能关召回）
  - Files: `app/search/sql_search.py`、`app/search/tokenize.py`
  - Verify: 用例 9 改写为「权重=0 时仅命中 breadcrumb 的行**不返回**」
- [ ] **T9 (P1, human: ~1d / CC: ~40min)** — 影响面补全 — `build_search_text` 返回元组波及 **11 个测试文件约 45 处 + `probe_jieba_terms.py`**；`build_embed_text` **4 个生产调用点**（补 `maintenance_routes.py:109`、`vector_search.py:141`）
  - Surfaced by: Section 5 + Codex CRITICAL-4
  - Files: `tests/conftest.py`、`tests/test_tokenize.py`、`tests/test_hybrid_search.py`、`tests/test_search.py`、`tests/test_health_check.py`、`tests/test_maintenance_badge.py`、`tests/test_qa_routes.py`、`tests/test_qa_status_filter.py`、`tests/test_search_lexicon_expand.py`、`tests/test_embed_text.py`、`scripts/probe_jieba_terms.py`、`app/routes/maintenance_routes.py`、`app/search/vector_search.py`
  - Verify: `pytest tests/ -v` 全绿（本项属大范围改动，跑全量）
- [ ] **T10 (P1, human: ~2h / CC: ~10min)** — 门禁 — **批二完成后必须全量重建向量**（`sync_with_db` 实测只补缺失 id，不会重嵌）
  - Surfaced by: Section 1/9 CRITICAL-5；`tests/test_reindex_vectors_script.py` 已为此设防
  - Files: `scripts/reindex_vectors.py` 调用点 + 验收清单
  - Verify: 重导后随机构造若干条文，断言其向量文本含面包屑
- [ ] **T11 (P2, human: ~4h / CC: ~20min)** — LanceDB — 抽 `_embedding_schema(dim)` 工厂消除**三份重复 schema**；补 `chunk_index`；**重建**现存表（LanceDB 不能 ALTER）；`search` 去重前**超取**；`hybrid_search` 去重取**最优**而非最后
  - Surfaced by: Section 5(a) + Codex CRITICAL-6
  - Files: `app/search/vector_search.py`、`app/routes/import_routes.py`、`app/routes/spec_routes.py`、`app/routes/maintenance_routes.py`、`app/search/hybrid_search.py`
  - Verify: 子块行数 +5~8%；按 `clause_id` 去重后覆盖率不缩水
- [ ] **T12 (P2, human: ~1h / CC: ~5min)** — `health_check.py` — `_vector_ids_and_state` 改为只 select `clause_id`（现为 `to_arrow()` 全列，2.4 万条时约 49 MB/次）
  - Surfaced by: Section 7 WARNING-10
  - Files: `app/maintenance/health_check.py`
  - Verify: 统计结果不变、无全列读
- [ ] **T13 (P2, human: ~2h / CC: ~10min)** — 重导流程 — **排除 `data/uploads/`**（147 个夹具 md）；收尾调用 LanceDB `optimize`/`cleanup`
  - Surfaced by: Section 9 CRITICAL + WARNING-11
  - Files: 重导脚本
  - Verify: 重导后规范数与上传目录内容无关；表版本数不随重导线性增长
- [ ] **T14 (P2, human: ~2h / CC: ~10min)** — 测试隔离 — 新增重导相关测试须同时 patch `DATABASE_PATH` / `LANCE_DB_PATH` / `UPLOAD_DIR`
  - Surfaced by: Section 6 WARNING-9（历史两次污染事故的同源成因）
  - Files: `tests/conftest.py`、新测试
  - Verify: 跑测试后真实库的 `rows` 与 `data/uploads/` 计数不变
- [ ] **T15 (P3, human: ~2h / CC: ~10min)** — UI — tooltip 改可见文字或 `aria-label`（`title` 对键盘/触屏不可达）；空面包屑降级显示；面包屑形态明确为**纯文本**；tooltip **移除「目次」**
  - Surfaced by: Section 11 WARNING-12 + 目次矛盾
  - Files: `app/templates/partials/`（检索过滤区、`clause_detail.html:55`）
  - Verify: 键盘 Tab 可达；空 `section_path` 不产出尾随分隔符
- [ ] **T16 (P3, human: ~1h / CC: ~5min)** — 文档 — `docs/standards/parser-判定规则与依据.md` 补 `section_path` **格式规范**一节（分隔符、编号是否入内、`X.0` 是否出现）；同步 `models.Clause`；说明 `parent_clause` 为死列
  - Surfaced by: Section 10 + WARNING-13
  - Files: `docs/standards/parser-判定规则与依据.md`、`app/models.py`
  - Verify: 新人可据该节复现面包屑文本
- [ ] **T18 (P2, human: ~1h / CC: ~5min)** — 重导流程 — **失败时明确报「本次重导未完成，请重跑」**，不得只把 task 标 `error` 让用户以为只是这一次尝试失败
  - Surfaced by: 未决项①核实结论——`rollback`（`import_routes.py:340`/`:602`）只覆盖 SQLite 事务，**LanceDB 向量写入不在事务内**，失败会留下常驻半成品态（SQLite 已回滚、向量已部分写入）
  - Files: `app/routes/import_routes.py`
  - Verify: 人为在向量写入阶段抛错，断言用户看到的进度消息含「未完成，请重跑」；并断言维护宫格此后报红点（`_count_vector_orphan`）

### 证据触发（未排期）

- [ ] **T17 (P?) — Task 3 目次对齐（降为证据触发）**：批一交付后读「面包屑覆盖率」的残余缺口。若缺口大（即存在正文标题真丢的节）→ 启用目次对齐；若缺口小 → 不做。证据：本次实测八个计划点名节点在正文中均可识别。
  - Files: 待定（`app/parser/toc.py`）
  - Verify: 启用与否由覆盖率数字决定，非主观判断

## Completion Summary

```
  +====================================================================+
  |            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
  +====================================================================+
  | Mode selected        | HOLD SCOPE                                 |
  | System Audit         | 15 文件/0 新类；分类引擎隐藏耦合；LanceDB 三份重复 schema |
  | Step 0               | HOLD SCOPE + 8 项决议（D2~D6.2）              |
  | Section 1  (Arch)    | 4 issues found (3 CRITICAL)                  |
  | Section 2  (Errors)  | 14 error paths mapped, 8 GAPS                |
  | Section 3  (Security)| 1 issue found, 0 High（伪风险删除后归零）      |
  | Section 4  (Data/UX) | 6 edge cases mapped, 4 unhandled             |
  | Section 5  (Quality) | 6 issues found                               |
  | Section 6  (Tests)   | Diagram produced, 3 gaps (2 CRITICAL)        |
  | Section 7  (Perf)    | 2 issues found                               |
  | Section 8  (Observ)  | 4 gaps found                                 |
  | Section 9  (Deploy)  | 4 risks flagged (1 CRITICAL)                 |
  | Section 10 (Future)  | Reversibility: 3/5, debt items: 4            |
  | Section 11 (Design)  | 3 issues (a11y / 面包屑形态 / 空态)            |
  +--------------------------------------------------------------------+
  | NOT in scope         | written (6 items)                            |
  | What already exists  | written (8 rows, 1 误报已更正)                 |
  | Dream state delta    | written                                      |
  | Error/rescue registry| 14 rows, 8 CRITICAL GAPS                     |
  | Failure modes        | 12 total, 8 CRITICAL GAPS                    |
  | TODOS.md updates     | 0 proposed (Task 3 为证据触发非 TODOS)         |
  | Scope proposals      | N/A (HOLD SCOPE)                             |
  | CEO plan             | skipped by mode                              |
  | Outside voice        | codex + completed                            |
  | Lake Score           | 5/5 answered coverage questions chose the 10/10 option |
  | Diagrams produced    | 5 (data flow, state machine, deploy, rollback, error flow) |
  | Stale diagrams found | 1 (database.py FTS 注释需补 breadcrumb 列)     |
  | Unresolved decisions | 0（两条均于 2026-09-27 复审核实关闭）              |
  +====================================================================+
```

### Unresolved Decisions

**两条均已核实并关闭（2026-09-27 复审）**：

1. ~~重导期间的检索可用性~~ → **已核实关闭**。已确认 `rollback`（`import_routes.py:340`/`:602`）只覆盖 SQLite 事务，**LanceDB 向量写入不在事务内**，故重导失败会留下常驻半成品态（SQLite 回滚但向量已部分写入）。但该状态**非静默**：`_count_vector_orphan`（`vids - db_ids`）会捕获并由维护宫格报红点，反向缺失亦由 `_count_vector_missing` 捕获。本计划提高该状态的发生概率（重导多三步），但检测机制已在。**唯一新增要求见 T18（失败措辞）**。
2. ~~`search.breadcrumb_weight` 写入侧鉴权~~ → **已核实关闭，虚警**。`users` 表无角色列（`id/username/password_hash/is_active/created_at`），全仓 `role ==` / `is_admin` / `require_admin` 零命中；`/maintenance/params/*` 由 `AuthMiddleware` 保护（豁免仅 `/login`、`/static`、`/health`），即任何已登录用户可写。**系统不存在「低权用户」概念，故越权面不成立。** 字面量回退路径删除后，本次改动无新增注入面。


## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | ISSUES OPEN | mode: HOLD_SCOPE, 8 critical gaps |
| Outside Review | `codex exec` (plan review) | Independent 2nd opinion | 1 | completed | 9 findings; 6 resolved as in-scope repairs; 2 became decisions D3.2/D4/D6.2; 2 remain unresolved |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** provider=codex, phase=plan-review, status=completed, findings=9. Codex 独立实测证伪了计划的 FTS5 语义断言，并发现影响面低估与 LanceDB 侧缺失；其「放弃 FTS 只进 embed_text」的战略建议经用户 D3.2 裁定为**不采纳**（用户确认要召回）。
- **CROSS-MODEL:** 两处独立一致：① `parent_path` 即分类输入（我与 Codex 各自发现）；② 拆批交付（我与 Codex 独立建议，用户采纳 D4）。一处分歧：Codex 建议放弃 Task 4，我基于「召回不可由应用层实现」保留之——**用户 D3.2 支持我的判断**。模型身份：native 为当前 harness（未报告具体模型），external 为 codex（`gpt-6-astra`）。
- **VERDICT:** CEO review NOT CLEARED — 8 CRITICAL GAPS 全部为静默路径（守恒断言缺失、分类漂移、FTS 空列、LanceDB 三处 schema、老行默认值、向量未重建、`sync_with_db` 只补缺失、重复条文号破坏 R14 分组）。**eng review required**。

NO UNRESOLVED DECISIONS
