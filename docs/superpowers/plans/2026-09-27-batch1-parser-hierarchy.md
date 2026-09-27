# 批一：解析器层级重建 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `parse_markdown` 用**一把尺子**推导层级、按 R14 兄弟多数表决判标题、让次分组单元与裸编号项的内容**回流到所属的「条」**，从而不再丢失 56% 正文的归属。

**Architecture:** 单遍扫描改为**两遍**：第一遍只收集候选行（编号、层级、父键、是否有自身正文），不做标题/正文判断；表决阶段按 `(层级, 父键)` 分组做兄弟多数表决（R14）；第二遍按表决结果 + 内节点判据（有无自身正文）生成条文，并用**栈式构建**生成 `section_path`。次分组单元（主控项目/一般项目）与裸编号项既不入祖先链也不成条文，于是它们与其后的 `检查数量/检验方法/表格` 连续落进所属的条。

**Tech Stack:** Python 3.14 / pytest / 纯标准库（`re`、`dataclasses`）。无新依赖。

**Spec:** `docs/superpowers/specs/2026-09-27-parser-tree-breadcrumb-chunk-design.md`；**修正以** `docs/superpowers/plans/2026-09-27-parser-tree-breadcrumb-chunk-plan.md` 顶部的「CEO 评审修正」横幅与文末「CEO 评审记录」为准（该文件另含本批的验收指标与证据）。

## Global Constraints

- **编码安全**：本批不引入任何外部输入解析路径；`parse_markdown` 的输入是用户上传文档的文本，所有正则必须避免灾难性回溯（不得出现嵌套量词作用于同一字符类）。
- **异常规范**：禁止裸 `except:`；本批新增代码不得新增 `except Exception`（既有 `health_check.py:68` 的裸捕获不在本批范围，见批二）。
- **资源释放**：本批不涉及文件/连接句柄。
- **性能**：`parse_markdown` 为 O(n) 两遍扫描；**循环内禁止 IO**。第二遍不得回查数据库。
- **常量集中**：所有黑名单/名单集合必须定义在模块顶层（`_NON_CLAUSE_*`、`_NON_LEVEL_GROUP_TITLES` 等），禁止散落。
- **TDD 流程**（全局规则 1.4）：先写失败测试 → 跑失败 → 最小实现 → 跑通过 → `pyright` 0 error → 提交。单次提交对应单个 Task。
- **提交信息**：`type: 描述`，type ∈ {feat, fix, test, docs, refactor, chore}。
- **测试隔离**（历史 learning 9/10）：本批新增测试若触及数据库，必须 `monkeypatch.setattr("app.database.DATABASE_PATH", ...)`；**本批主体是纯函数测试，不得引入数据库依赖**。
- **不得放宽既有断言（工程评审 C11 修正）**：本批会改变 `parse_markdown` 的产出结构，
  受影响断言**不止「层级尺子」那 2 处**。实施时必须逐条判断「按设计变更」还是「真回归」，
  并在提交信息中说明理由。**已知会变化**（Task 1 与 Task 3 的 Step 1 各自列出）：

  | 既有用例 | 变化原因 |
  |---|---|
  | `test_parse_markdown_levels` | 层级尺子由 `#` 计数改为编号点数（`5.1.1`: 4 → 3） |
  | `test_fullwidth_dot_level_inference` | 同上 |
  | `test_parse_non_hash_numbered_clauses` | 正文型行现在**总是**产出（无条件 flush） |
  | `test_fullwidth_dot_body_clause_parsed` | 同上 |
  | `test_multi_space_title_cleanup` | 标题型行恢复 `_clean_title`（C5） |

  **不得放宽**的是：`test_parse_markdown_parent_inheritance`、`test_parse_appendix_clauses`、
  `test_parse_qianyan_retained_and_marked`、`test_parse_tiaowenshuoming_body_rows_inherit`、
  `test_parse_normal_clause_has_is_non_clause_false` —— 它们描述的行为必须**原样保留**
  （前两者曾因断言过松（`len>=2` / `any(...)`）而未能拦住 `parent_path` 含自身的漂移，
  实施时**应把它们收紧为等值断言**，不要照旧宽松）。
  收紧后的 `parent_path` 断言见 Task 3 Step 1 的 `test_parent_path_excludes_self`。

---

## File Structure

| 文件 | 责任 | 本批动作 |
|---|---|---|
| `app/parser/md_parser.py` | 唯一的解析实现；本批的**全部**生产改动集中于此 | 重构 |
| `tests/test_md_parser.py` | 解析器单元测试（既有 452 行） | 修改既有断言 + 新增用例 |
| `scripts/survey_structure.py` | **新建**：结构勘察 + 三项回归指标 + 面包屑覆盖率（Task 8） | 新建 |
| `tests/test_parse_conservation.py` | **新建**：守恒断言（Task 9） | 新建 |
| `tests/test_classify_baseline.py` | **新建**：分类标签分布回归（Task 10） | 新建 |
| `tests/fixtures/cjj2_source.md` | **新建**：CJJ2 源 md 的固定副本，供守恒/覆盖率测试使用（不依赖 `data/outputs/`） | 新建 |

> **为何固定夹具副本**：`data/` 是运行时目录，测试不得依赖它（且历史上 `data/uploads/` 已被 pytest 污染）。夹具取 `data/outputs/f543577f/f543577f.md` 的当前内容，一次性拷入 `tests/fixtures/`。

---

## 执行顺序（工程评审 R1 + R2 决议，2026-09-27）

本计划共 12 个 Task 编号，其中 **Task 5 与 Task 6 已不再实施**（R2 决议删除/合并）。
Task 编号**不按执行顺序**，**必须按本表执行**：

| 序 | Task | 状态 | 为何在此位置 |
|---|---|---|---|
| 1 | Task 1 层级的单一来源 + 小修复 | 实施 | 定义层级尺子与模块级辅助函数 |
| 2 | Task 2 R3 的 0 段不构成节点 | 实施 | 判据，被 Task 3 消费 |
| 3 | **Task 4** 裸编号项不作为条文 | 实施 | 收窄「什么算候选行」——必须在主循环改动**之前** |
| 4 | **Task 3** 解析主循环四处改动 | 实施 | **批一的核心**：R14 预算投票 + 无条件 flush + 内节点判据 + `section_path` |
| 5 | ~~Task 5~~ | **已删除** | R2 决议：两遍重写层不实施（见该 Task 的删除理由） |
| 6 | ~~Task 6~~ | **已合并入 Task 3** | 内节点判据与 `section_path` 属同一次主循环改动，拆开会造成前一个 Task 的检查点无法成立 |
| 7 | Task 7 非条文块名单扩充 | 实施 | 独立（谓词扩充） |
| 8 | Task 8 勘察脚本 + 五项指标 | 实施 | 需要 Task 1–4 的产出 |
| 9 | Task 9 守恒断言（含变异验证） | 实施 | 门禁 |
| 10 | Task 10 分类标签分布回归基线 | 实施 | 门禁 |
| 11 | Task 11 重复条文号诊断 | 实施 | 诊断 |
| 12 | Task 12 批一验收 | 实施 | 收口 |

> **为何 Task 4 在 Task 3 之前**：Task 4 决定「哪些行算候选行」，Task 3 的投票与
> 主循环都建立在这个集合上。先收窄集合、再改循环，可以少写一遍。
>
> **为何 Task 3 是单个 Task 而非拆开**：R14 投票、无条件 flush、内节点判据、
> `section_path` 四者都发生在**同一次主循环改动**内（`flush()` 的产出条件与
> 祖先链取用）。R1 曾把它们拆成 Task 5/6 两个 Task，结果前一个 Task 因后一个
> Task 的过滤缺失而无法通过自己的检查点——R2 据此合并。

## Task 1: 层级的单一来源 + 三个小修复

**Files:**
- Modify: `app/parser/md_parser.py:7-14`（`_NUM_PATTERNS`）、`:309-321`（`_extract_clause_no`）、`:213-217`（`#` 路径）
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `_NUM_PATTERNS: list[str]` — **改为纯字符串列表**（去掉 `level_base`）
  - `_level_from_clause_no(clause_no: str) -> int` — 层级唯一来源
  - `_extract_clause_no(raw_title: str) -> str | None` — **返回 `None` 表示「匹配不到编号」**

- [ ] **Step 1: 先修既有断言（层级尺子按设计变更）**

`docs/superpowers/specs/2026-09-27-...-design.md` §4.1① 明确「层级只由编号点数推导，不再用 `#` 数量」，因此下列既有断言测的是**旧尺子**，必须随设计变更：

| 位置 | 旧断言 | 新断言 | 变更理由 |
|---|---|---|---|
| `tests/test_md_parser.py:76` | `levels["5.1.1"] == 4` | `== 3` | `5.1.1` 有 2 个点 → 新尺子 = 1+2 = 3；旧值 4 来自 `####` 的井号数 |
| `tests/test_md_parser.py::test_fullwidth_dot_level_inference` | 按井号数断言 | 同规则改为按点数 | 同上 |

```python
def test_parse_markdown_levels():
    results = parse_markdown(SAMPLE_MD)
    levels = {r["clause_no"]: r["level"] for r in results}
    # 层级 = 1 + 编号点数（唯一尺子）；5.1.1 两个点 → 3
    assert levels["5.1.1"] == 3
    assert levels.get("5.1") is None  # 中间标题不生成条文（无自身正文 → 内节点）
```

- [ ] **Step 2: 跑测试确认它们按新尺子失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v -k "level"`
Expected: FAIL — `assert 4 == 3`

- [ ] **Step 3: 实现单尺子层级 + 三个小修复**

```python
# 编号行前缀正则（按匹配优先级排列）
# 点号兼容半角 `.` 与全角 `．`（U+FF0E，OCR 高频把条文号点误识别为全角）
_NUM_PATTERNS = [
    # 中文附录: 附录A, 附录B.1
    r'^(附录[A-Z]+(?:[\.．][\d]+)*)\s+(.+)',
    # 字母+数字编号: D.4, D.4.1, A.1, TB.10423 (字母后跟数字，可选点分隔)
    r'^([A-Z]+(?:[\.．]?\d+)+)\s+(.+)',
    # 纯数字编号: 1, 3.1, 1.0.1, 5.0.3
    r'^(\d+(?:[\.．]\d+)*)\s+(.+)',
]

# 层级上限（与旧实现一致的封顶，避免异常输入产生超深层级）
_MAX_LEVEL = 6


def _level_from_clause_no(clause_no: str) -> int:
    """层级唯一来源：编号的点数。整份文档只有这一把尺子。

    R1（182 号第三十二~四十一条）/ GB/T 1.1：层级由编号的段数唯一决定，
    与 Markdown 的 `#` 数量无关——PaddleOCR-VL 的 `#` 数量不稳定，双尺子会
    让 `### 18.3`（旧=3）与 `18.3.1`（旧=3）撞层，导致栈被提前 pop。

    例：'5'→1, '5.1'→2, '5.1.1'→3, '附录A'→1, 'A.1.3'→3, '16.0.2'→3
    """
    return min(1 + clause_no.count('.'), _MAX_LEVEL)


def _extract_clause_no(raw_title: str) -> str | None:
    """从标题中提取条文号；**匹配不到编号返回 None**（不再兜底返回整串标题）。

    R1 ⑦：旧实现兜底 `return title` 会把 `### 某英文标题` 变成 clause_no，
    导致英文标题成条文、以及 `Ⅰ 主控项目` 这类非条文号入库（实测 124 条伪条文号）。

    兼容全角点号（U+FF0E），提取后统一归一化为半角点号。
    """
    for pattern in _NUM_PATTERNS:
        m = re.match(pattern, raw_title)
        if m:
            return m.group(1).replace('．', '.')
    return None


# 裸露的 4 位年份（如封面页的 "2008"）不是条文号
_BARE_YEAR = re.compile(r'^(19|20)\d{2}$')
```

`#` 路径补中文检查（`_match_clause_line` 早有 `re.search(r'[一-鿿]')`，`#` 路径漏了）：

```python
            if m_hash:
                raw_title = m_hash.group(2).strip()
                clause_no = _extract_clause_no(raw_title)
                # 匹配不到编号，或编号是裸露年份，或标题无中文 → 不当条文
                if clause_no is None or _BARE_YEAR.match(clause_no):
                    continue
                if not re.search(r'[一-鿿]', raw_title):
                    continue
                level = _level_from_clause_no(clause_no)
```

> **⚠️ 工程评审修正 SC-2（P1）**：`_NUM_PATTERNS` 由 `(pattern, level_base)` 二元组改为**纯字符串**后，
> 全仓共有 **三处**解包它的循环，初稿只改了 `_extract_clause_no` 一处：
>
> ```
> app/parser/md_parser.py:113   for pattern, level_base in _NUM_PATTERNS:   ← _match_clause_line
> app/parser/md_parser.py:299   for pattern, _ in _NUM_PATTERNS:            ← _extract_title
> app/parser/md_parser.py:314   for pattern, _ in _NUM_PATTERNS:            ← _extract_clause_no（已改）
> ```
>
> 另两处不改会因「字符串解包成两个名字」抛 `ValueError`。三处**全部**改为 `for pattern in _NUM_PATTERNS:`；
> `_match_clause_line` 内原先用 `level_base` 算层级的那一行同时删除（层级改由 `_level_from_clause_no` 提供）。
> Step 5 增加一条源码断言把它钉死。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: PASS

- [ ] **Step 5: 补三个小修复的针对性用例 + 三处解包点的源码断言**

```python
def test_all_num_patterns_loops_use_single_unpack():
    """`_NUM_PATTERNS` 已是纯字符串列表；三处循环都不得再解包成两个名字。

    初稿只改了 _extract_clause_no，漏掉的 :113 与 :299 会抛 ValueError。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app/parser/md_parser.py").read_text(encoding="utf-8")
    assert "for pattern, level_base in _NUM_PATTERNS" not in src
    assert "for pattern, _ in _NUM_PATTERNS" not in src
    assert src.count("for pattern in _NUM_PATTERNS") == 3
```


```python
def test_hash_heading_without_clause_no_is_not_clause():
    """# 路径匹配不到编号即不当条文（旧实现兜底 return title 会造出伪条文号）"""
    md = "# Code for construction and quality acceptance of bridge works\n\n正文。\n"
    assert parse_markdown(md) == []

def test_bare_year_is_not_clause_no():
    """裸露 4 位年份不得成为条文号"""
    md = "2008\n\n正文内容。\n"
    assert all(r["clause_no"] != "2008" for r in parse_markdown(md))

def test_level_comes_from_number_not_hash_count():
    """层级只由编号点数推导，与井号数量无关"""
    md = "### 5.1.1 一般规定\n\n内容甲。\n\n######## 5.1.2 模板安装\n\n内容乙。\n"
    levels = {r["clause_no"]: r["level"] for r in parse_markdown(md)}
    assert levels["5.1.1"] == 3
    assert levels["5.1.2"] == 3   # 井号数不同，层级相同
```

- [ ] **Step 6: pyright + 提交**

Run: `D:/Python/python.exe -m pyright app/parser/md_parser.py`（仓库根跑，0 error）
```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "refactor: 层级改为只由编号点数推导（单一尺子）+ 修三处编号识别小缺陷

层级原由 # 数量推导，与编号点数构成双尺子，导致 ### 18.3 与 18.3.1 撞层、
栈被提前 pop（实测 18.3.1 的父链为 ['合龙段的长度宜为 2m','悬索桥']）。
随设计变更更新 2 处测旧尺子的断言，并补 # 路径中文检查、编号兜底返回 None、
裸露年份排除。"
```

---

## Task 2: R3 的 0 段不构成层级节点

> **⚠️ 工程评审修正 SC-1（P0，2026-09-27）**：本 Task 初稿的判据是
> `len(parts) > 1 and '0' in parts`（任意段为 0），它会把 **`3.0.1` 这类合法的「条」整体跳过**。
> 实测规模：**JGJ107 39 条 / 44,353 字符（占其 content 的 80%）、CJJ2 44 条 / 12,796 字符**。
> 182 号第三十五条说的是「条编号中**对应节的编号**用 0 表示」——即 `3.0.1` **本身是条**，
> 只是不存在 `3.0` 这个「节」节点。判据必须收窄为**末段为 0**（见下方 Step 3）。
> 本 Task 自己的测试 `test_zero_segment_is_not_a_node` 断言 `3.0.1` 必被产出，
> 与初稿判据直接矛盾——评审据此定位到该缺陷。

**Files:**
- Modify: `app/parser/md_parser.py`（新增判定函数，Task 5 的第二遍会调用）
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: `_level_from_clause_no`（Task 1）
- Produces: `_is_zero_segment_node(clause_no: str) -> bool` —— **末段为 `'0'` 才是节位占位**

- [ ] **Step 1: 写失败测试**

```python
def test_zero_segment_is_not_a_node():
    """R3：章内不分节时条编号的节位用 0 表示（3.0.1）。该 0 段不构成节点。

    关键：`3.0.1` **本身是合法的条**，必须照常入库；只有末段为 0 的 `3.0`
    （节位占位，真实文档里通常不出现）才不是节点。
    """
    md = ("### 3 章名\n\n### 3.0 不应存在的节\n\n"
          "3.0.1 接头设计应满足强度要求。\n")
    results = parse_markdown(md)
    r = [c for c in results if c["clause_no"] == "3.0.1"]
    assert len(r) == 1                            # ← 3.0.1 必须被产出（不是被跳过）
    assert r[0]["parent_path"] == ["章名"]         # 父链里没有 `3.0` 那一级
    assert all(c["clause_no"] != "3.0" for c in results)  # 3.0 本身不入库

def test_zero_segment_predicate_is_last_segment_only():
    """判据只看**末段**：`3.0` 是节位占位；`3.0.1` 是条，不得被误伤。

    实测：若按「任意段为 0」判定，JGJ107 会丢掉 39 条 / 44,353 字符（占其 80%）。
    """
    assert _is_zero_segment_node("3.0") is True
    assert _is_zero_segment_node("1.0") is True
    assert _is_zero_segment_node("3.0.1") is False      # ← 条，不是节点
    assert _is_zero_segment_node("1.0.2") is False
    assert _is_zero_segment_node("10.1") is False       # 10 不是 0
    assert _is_zero_segment_node("3") is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "zero_segment" -v`
Expected: FAIL — `ImportError: cannot import name '_is_zero_segment_node'`

- [ ] **Step 3: 实现**

```python
def _is_zero_segment_node(clause_no: str) -> bool:
    """R3（182 号第三十五条）：章内不分节时，条编号中**对应节的编号**用 "0" 表示。

    因此 `X.0.Y` 里的 `0` 段表示「本章不分节」：它不构成一个层级节点，
    所以不存在 `3.0` 这个「节」，`3.0.1` 的父链直接是 [`3 章名`]。

    ⚠️ 判据只能看**末段**：末段为 `'0'`（如 `3.0`）才是节位占位、不当节点；
    `3.0.1` / `1.0.2` 的末段是条号，它们**本身是条，必须照常入库**。
    若写成「任意段为 0」，会把所有 `X.0.Y` 条文一起丢掉——实测 JGJ107
    39 条 / 44,353 字符（占其 content 80%）、CJJ2 44 条 / 12,796 字符。
    """
    parts = clause_no.split('.')
    return len(parts) > 1 and parts[-1] == '0'
```

**并让旧循环消费它**（约 1 行）——否则本 Task 的行为断言在其检查点上不可能通过：

```python
                # 在 `#` 路径算出 clause_no 之后、进黑名单链路之前。
                # ⚠️ **必须带 None 守卫**：Task 1 已把 `_extract_clause_no` 改为
                # 匹配不到返回 `None`，而 `### 前言` 这类标题正是 None——
                # 漏掉这半个守卫会 `AttributeError: 'NoneType' object has no attribute 'split'`，
                # 直接打挂明令不得弱化的 `test_parse_qianyan_retained_and_marked`。
                if clause_no is not None and _is_zero_segment_node(clause_no):
                    continue        # R3：节位为 0 的占位号不成节点
```

> **⚠️ SDD 实施裁定 R-T2**：`_is_zero_segment_node` 的**最终消费者是 Task 3 的 `_candidate_of`**，
> 但本 Task 的行为断言（`3.0` 不入库、`3.0.1` 父链为 `["章名"]`）要求该规则**在本 Task 就生效**。
> 若只在旧循环里加这一行，Task 3 实施时它会被 `_candidate_of` 取代（**预期取代**，见 F1 先例）。
> 这约 2 行一次性代码是「每个 Task 的检查点必须绿色」的代价，可接受。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "zero_segment" -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "feat: R3 的 0 段不构成层级节点（判据只看末段）

按 182 号第三十五条，章内不分节时条编号的节位用 0 表示，故不存在 `3.0`
这个节节点，`3.0.1` 的父链直接是 [3 章名]。判据只看末段——`3.0.1` 本身
是合法的条，必须照常入库。工程评审 SC-1 修正：初稿「任意段为 0」的写法会
让 JGJ107 丢掉 39 条 / 44,353 字符（占其 80%）。"
```

---

## Task 3: 解析主循环 —— R14 预算投票 + 无条件 flush + 内节点判据 + `section_path`

> **本 Task 是批一的核心，也是唯一改动解析主循环的地方。**
> 工程评审 **R2 决议**（2026-09-27）：保留旧的单遍结构，只做四处改动，
> **不引入两遍重写层**（不引入 `_RawLine` / `_collect_candidates` / `_parse_two_pass` / `own_body`）。
>
> **执行时点**：本 Task 在 **Task 4（裸编号项不作为条文）之后**执行——Task 4 收窄了
> 「什么算候选行」，而本 Task 的投票与主循环都建立在这个集合上。

**Files:**
- Modify: `app/parser/md_parser.py`（`parse_markdown` 主循环 + 新增 `_candidate_of` / `_vote_title_mode` / `_parent_key` / `_build_section_path`）
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: `_level_from_clause_no`（Task 1）、`_extract_clause_no`（Task 1）、`_is_zero_segment_node`（Task 2）、`_match_clause_line`（Task 4）
- Produces:
  - `_candidate_of(line: str) -> tuple[int, str, str] | None` —— `(level, clause_no, tail)`
  - `_vote_title_mode(lines: list[str]) -> dict[tuple[int, str], bool]`
  - `_parent_key(stack: list[dict], level: int) -> str`
  - `_build_section_path(ancestors: list[dict]) -> str`
  - `parse_markdown(md_text: str) -> list[dict]`（签名不变；**新增 `section_path` 键**）

- [ ] **Step 1: 写失败测试（四组，一次写齐）**

```python
# ── 组 1：R14 兄弟多数表决 ──
def test_r14_sibling_majority_rescues_long_untitled_clause():
    """R14 / GB/T 1.1 §7.3.3：同层各条有无标题应一致。

    `3.0.1 接头设计应满足强度及变形性能的要求` 旧实现按 ≤20 字判为标题型；
    同层 3.0.2~3.0.9 皆为无标题条 → 多数表决判它也无标题 → 正文归其自身。
    实测背景：JGJ107 库里 3.0.1 缺失而 3.0.2~3.0.9 都在（都是 title='' 的正文型）。
    """
    md = "\n\n".join(
        ["3.0.1 接头设计应满足强度及变形性能的要求"] +
        [f"3.0.{i} 接头安装应符合本规程第{i}章的规定。" for i in range(2, 10)]
    )
    r = [c for c in parse_markdown(md) if c["clause_no"] == "3.0.1"]
    assert len(r) == 1
    assert r[0]["title"] == ""
    assert "接头设计应满足强度及变形性能的要求" in r[0]["content"]

def test_r14_group_of_titles_stays_titles():
    md = "3.0.1 一般规定\n\n正文甲。\n\n3.0.2 材料要求\n\n正文乙。\n\n3.0.3 检验方法\n\n正文丙。\n"
    titles = {c["clause_no"]: c["title"] for c in parse_markdown(md)}
    assert titles["3.0.1"] == "一般规定"
    assert titles["3.0.3"] == "检验方法"

def test_tie_prefers_untitled():
    """平票一律判「无标题」——保内容优先（工程评审 SC-6）

    2 条组 1:1 时若回退首元素且它像标题，则整组判标题型，组内无自身正文的
    那条会按内节点被丢弃、其 tail 文本消失。
    """
    md = "3.0.1 一般规定\n\n3.0.2 接头安装应符合本规程的规定。\n"
    clauses = {c["clause_no"]: c for c in parse_markdown(md)}
    assert clauses["3.0.2"]["title"] == ""
    assert "接头安装应符合本规程的规定" in clauses["3.0.2"]["content"]

# ── 组 2：无条件 flush（救回「标题型且无后续内容」的行） ──
def test_body_type_clause_emitted_without_following_lines():
    """正文型编号行即使后面没有任何内容行也必须产出。

    旧实现只在 `current_content_lines` 非空时才结算，于是 `3.0.1` 这类
    「标题型且无后续内容」的行**从未被结算**、直接从库里消失。
    """
    md = "3.0.1 接头设计应满足强度及变形性能的要求\n\n3.0.2 钢筋连接用套筒应符合规定。\n"
    clauses = {c["clause_no"]: c for c in parse_markdown(md)}
    assert "3.0.1" in clauses
    assert "接头设计应满足强度及变形性能的要求" in clauses["3.0.1"]["content"]

def test_body_type_clause_keeps_tail_in_content():
    """既有行为不得回归：正文型编号行的编号后文本进 content、title 为空"""
    md = "1.0.1  为在混凝土结构中使用钢筋机械连接，制定本规程。\n"
    r = parse_markdown(md)
    assert r[0]["title"] == ""
    assert r[0]["content"] == "为在混凝土结构中使用钢筋机械连接，制定本规程。"

# ── 组 3：内节点判据 + R7 内容回流（56%） ──
def test_inner_node_without_body_is_not_emitted_but_serves_as_ancestor():
    """内节点判据 = 有无自身正文（不是「是章/节还是条」）"""
    md = ("## 6 混凝土分项工程\n\n### 6.1 模板\n\n"
          "#### 6.1.1 一般规定\n\n模板及其支架应进行设计。\n")
    nos = [c["clause_no"] for c in parse_markdown(md)]
    assert "6" not in nos and "6.1" not in nos       # 无自身正文 → 只作祖先
    assert "6.1.1" in nos

def test_appendix_with_own_body_is_a_leaf_clause():
    """附录A 有自身正文（CJJ2 是 35K 字符验收记录表）→ 叶条文，入库可检索（R11）"""
    md = "附录A 验收记录表\n\n<table><tr><td>序号</td><td>项目</td></tr></table>\n"
    r = [c for c in parse_markdown(md) if c["clause_no"] == "附录A"]
    assert len(r) == 1
    assert "序号" in r[0]["content"]

def test_page_marker_isolates_pages():
    """`## 第X页` 必须自成一格，使封面/前引文字**不并入首条真条文**。

    契约出处：`app/parser/ocr_clean.py:11-12`（明文承诺保留该标记）。
    失败形态（Task 1 复核 Important #2 探针复现）：页标记被筛掉后，前引文字
    并入 `1 总则`，使 `is_cover_clause()` 对其返回 True（特征词 ≥2）→
    `_filter_cover_clauses`（`import_routes.py:381`）把首条真条文**连同其正文**丢弃。
    """
    md = ("## 第1页\n\nICS 77.140.60\n\n中华人民共和国国家标准\n\n代替 GB/T 1499.1-2008\n\n"
          "## 1 总则\n\n1.0.1 正文内容。\n")
    clauses = {c["clause_no"]: c for c in parse_markdown(md)}
    # 页标记自身保留但隐藏（is_non=1），前引文字归它，不污染 `1`
    page = [c for c in clauses.values() if c["clause_no"] == "第1页"]
    assert len(page) == 1 and page[0]["is_non_clause"] is True
    assert "ICS" in page[0]["content"]
    # 首条真条文不得带上封面特征词（否则会被 _filter_cover_clauses 误删）
    assert clauses["1"]["content"].strip() == ""
    from app.parser.md_parser import is_cover_clause
    assert is_cover_clause(clauses["1"]["content"]) is False
    assert clauses["1.0.1"]["content"].strip() == "正文内容。"


def test_group_heading_content_flows_to_enclosing_clause():
    """R7 次分组单元的内容回流到所属条（实测规模：252,514 字符 / 占全部正文 56%）

    `#### 主控项目` / `#### 一般项目` 在 Task 1 之后已**不是候选行**（无编号且
    非非条文块），裸编号项在 Task 4 之后也不是——于是它们与其后的
    `检查数量：` / `检验方法：` / 表格**连续落进所属的条** `14.3.1`。
    """
    md = """### 14.3 检验标准

14.3.1 钢梁制作质量检验应符合下列规定：

#### 主控项目

1 钢材的品种、规格应符合设计要求。

检查数量：全数检查。

检验方法：检查质量证明文件。

#### 一般项目

6 焊缝外观质量应符合本规范第14.2.7条规定。

检查数量：同类部件抽查10%。

14.3.2 钢梁现场安装检验应符合下列规定：

正文乙。
"""
    clauses = {c["clause_no"]: c for c in parse_markdown(md)}
    body = clauses["14.3.1"]["content"]
    assert "钢材的品种" in body and "焊缝外观质量" in body
    assert "检查数量：全数检查。" in body and "检验方法：检查质量证明文件。" in body
    assert "主控项目" in body and "一般项目" in body      # 标签文本不丢，只是不再是节点
    assert "####" not in body                              # 井号标记必须剥掉
    assert clauses["14.3.2"]["content"].strip() == "正文乙。"

# ── 组 4：section_path / parent_path（**祖先链不含自身**） ──
def test_section_path_includes_each_ancestor_with_number():
    md = "## 6 混凝土分项工程\n\n### 6.1 模板\n\n#### 6.1.1 一般规定\n\n正文甲。\n"
    r = [c for c in parse_markdown(md) if c["clause_no"] == "6.1.1"][0]
    assert r["section_path"] == "6 混凝土分项工程 > 6.1 模板"     # ← 不含自身

def test_section_path_has_no_trailing_separator_when_root():
    r = parse_markdown("1.0.1 正文甲。\n")[0]
    assert r["section_path"] == ""

def test_no_cross_chapter_leak():
    md = ("## 6 混凝土分项工程\n\n### 6.1 模板\n\n"
          "#### 6.1.1 一般规定\n\n正文甲。\n\n"
          "## 7 预应力分项工程\n\n7.0.1 预应力筋应抽样检验。\n")
    r = [c for c in parse_markdown(md) if c["clause_no"] == "7.0.1"][0]
    assert "混凝土" not in r["section_path"]
    assert "预应力" in r["section_path"]

def test_parent_path_excludes_self():
    """`parent_path` **不含自身**——与旧实现一致，保 `classify_clause` 的输入不变。

    `parent_path` 会被 `import_routes.py:469` 送进 `classify_clause` 作为规则
    匹配文本的一部分（`rule_engine.py:66` 的 `augmented_text`），语义漂移会
    直接改变 dim 得分与标签（工程评审 CRITICAL-2 / C3）。
    """
    md = "## 6 混凝土分项工程\n\n### 6.1 模板\n\n#### 6.1.1 一般规定\n\n正文甲。\n"
    r = [c for c in parse_markdown(md) if c["clause_no"] == "6.1.1"][0]
    assert r["parent_path"] == ["混凝土分项工程", "模板"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "r14 or tie_prefers or body_type or inner_node or appendix_with_own or group_heading or section_path or cross_chapter or excludes_self" -v`
Expected: FAIL — `KeyError: 'section_path'`；`3.0.1` 缺失

- [ ] **Step 3: 实现**

```python
# OCR 管线的页分隔标记（`## 第X页`）。它必须被 parse_markdown 保留为独立候选，
# 否则每页不再隔离——见 `_candidate_of` 里 (a) 的说明与 `ocr_clean.py:11-12` 的契约。
_PAGE_MARKER = re.compile(r'^第\s*\d+\s*页$')


def _candidate_of(line: str) -> tuple[int, str, str] | None:
    """识别候选行 → `(level, clause_no, tail)`；不是候选行返回 `None`。

    **预扫投票（`_vote_title_mode`）与正式解析必须共用本函数**：两者判据若
    不一致，投票结果会对不上正式解析的那一行，兄弟表决就失去意义。
    """
    if not line.strip():
        return None
    m_hash = re.match(r"^(#{1,6})\s+(.+)$", line)
    if m_hash:
        raw_title = m_hash.group(2).strip()
        clause_no = _extract_clause_no(raw_title)
        if clause_no is None:
            # 无编号标题：以下三类保留为候选，其余（如英文标题）不当条文（R1 ⑦）。
            t = _clean_title(_extract_title(raw_title))
            # (a) 页分隔标记（`## 第X页`）：**必须保留为候选**，以维持「每页独立隔离」的契约
            # ——`app/parser/ocr_clean.py:11-12` 明文承诺「保留它才能让 parse_markdown
            # 为封面页生成独立条文，再由 import 侧的 is_cover_clause 丢弃」。
            # 若它被筛掉：封面/前引文字会并入**首条真条文**，而 `is_cover_clause()`
            # 可能对该首条返回 True（实测特征词 ≥2 即 True）→ `_filter_cover_clauses`
            # （`import_routes.py:381`）把首条真条文连同其正文一并丢弃。
            # （Task 1 复核 Important #2，探针复现；旧实现靠该标记自成一格来隔离。）
            # 注意它必须走**本分支**：Task 1 已删掉 `第…[节章条]` 兜底，故
            # `_extract_clause_no("第1页")` 返回 None。处置：保留为候选，并在主循环里
            # 标 is_non_clause=1（隐藏不检索），等价于旧行为「自成一格 → 被封面过滤丢弃」，
            # 但不再依赖封面特征词。
            if _PAGE_MARKER.match(t):
                return (1, t, t)
            # (b) 法定非条文块（前言/条文说明/公告/引用标准名录/用词说明…）
            return (1, t, t) if is_non_clause_title(t) else None
        if _BARE_YEAR.match(clause_no):
            return None
        title_txt = _clean_title(_extract_title(raw_title))
        # ⚠️ 本分支的判据顺序是**承重**的，四处顺序都不可随意调换：
        #   ① 目次/Contents（过滤类）必须先于「无中文」检查 —— `Contents` 是英文、
        #      无中文，若先做中文检查会把它筛掉，于是主循环里的 `is_filter_non_clause_title`
        #      永远不触发、`discard_section` 从未置位，目录行会泄漏进下一条正文
        #      （Task 1 复核 Important #1，已探针复现）。
        #   ② 非条文块（前言/条文说明/公告…）必须先于 0 段检查 —— 否则
        #      `### 1.0 条文说明` 这种「占位号 + 非条文标题」会被整条丢掉，
        #      而不是以 is_non_clause=1 保留（Task 2 复核 Minor #1）。
        #   ③ 0 段检查必须在 `#` 分支**存在** —— 否则 Task 3 删掉 Task 2 的脚手架行后，
        #      `### 3.0` 重新成为节点，`test_zero_segment_is_not_a_node` 在本 Task
        #      的检查点上失败（Task 2 复核 Minor #2，前瞻性缺陷）。
        #   ④ 无中文检查最后。
        if is_filter_non_clause_title(title_txt):
            return (_level_from_clause_no(clause_no), clause_no, title_txt)
        if is_non_clause_title(title_txt):
            return (_level_from_clause_no(clause_no), clause_no, title_txt)
        if _is_zero_segment_node(clause_no):
            return None
        if not re.search(r'[一-鿿]', raw_title):
            return None
        return (_level_from_clause_no(clause_no), clause_no, title_txt)
    m_num = _match_clause_line(line.strip())
    if not m_num:
        return None
    clause_no, tail = m_num                      # Task 4 之后的返回形状
    if _is_zero_segment_node(clause_no):         # R3：末段为 0 的节位占位
        return None
    return (_level_from_clause_no(clause_no), clause_no, tail)


def _parent_key(stack: list[dict], level: int) -> str:
    """父键 = 栈中层级**严格小于** `level` 的最深候选行的 `clause_no`。

    投票与正式解析**共用本函数**，保证分组键一致。
    """
    for entry in reversed(stack):
        if entry["level"] < level:
            return entry["clause_no"]
    return ""


def _vote_title_mode(lines: list[str]) -> dict[tuple[int, str], bool]:
    """R14 兄弟多数表决（预扫，只读，不改任何状态）。

    按 `(层级, 父键)` 分组；父键只用「编号 + 层级」推导，与标题/正文判定无关，
    因此可在正式解析之前算准。组内多数决定该组是「带标题条」还是「无标题条」。

    平票（偶数条且恰好半数）**一律判「无标题」**：判「无标题」时该行文本进入
    自身 content，不会丢；若回退首元素且它像标题，则整组判标题型，组内无自身
    正文的那条会按内节点被丢弃（工程评审 SC-6）。
    """
    rows: list[tuple[int, str, str]] = []
    stack: list[dict] = []
    for line in lines:
        cand = _candidate_of(line)
        if cand is None:
            continue
        level, clause_no, tail = cand
        rows.append((level, _parent_key(stack, level), tail))
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        stack.append({"level": level, "clause_no": clause_no})

    groups: dict[tuple[int, str], list[str]] = {}
    for level, parent_key, tail in rows:
        groups.setdefault((level, parent_key), []).append(tail)

    verdict: dict[tuple[int, str], bool] = {}
    for key, tails in groups.items():
        yes = sum(1 for t in tails if _looks_like_title(t))
        # 平票 → False（无标题，保内容）；否则严格的多数
        verdict[key] = (yes * 2 > len(tails)) if yes * 2 != len(tails) else False
    return verdict


def _build_section_path(ancestors: list[dict]) -> str:
    """由**祖先**（不含自身）构建面包屑快照：`"6 混凝土分项工程 > 6.1 模板"`。

    非条文块（前言/条文说明等）不进面包屑——它们不是结构层级。
    无祖先返回**空串**（不带尾随分隔符：详情弹窗与批二的 FTS `breadcrumb` 列
    都会直接使用该串）。
    """
    return " > ".join(
        f"{a['clause_no']} {a['title']}".strip()
        for a in ancestors if not a.get("is_non_clause")
    )
```

主循环：保留旧的单遍结构，**四处改动**：

```python
def parse_markdown(md_text: str) -> list[dict]:
    if not md_text.strip():
        return []
    lines = md_text.split("\n")
    title_mode = _vote_title_mode(lines)        # 改动①：R14 预扫投票

    clauses: list[dict] = []
    stack: list[dict] = []          # 标题栈；只放候选行，末位即「当前条」
    pending: list[str] = []         # 当前条的待落内容
    discard_section = False         # 目次段：段内一切丢弃
    inherit_non_clause = False      # 条文说明段：正文型行继承打标

    def flush() -> None:
        """结算「当前条」= `stack[-1]`。

        改动② **无条件结算**：旧实现只在 `current_content_lines` 非空时才结算，
        于是「标题型且无后续内容」的行从未被结算——实测 3.0.1 就是这样消失的。

        改动③ **内节点判据**：无自身正文者只作祖先、不入库。
          - 标题型且有后续内容 → 有自身正文 → 叶条文
          - 标题型且无后续内容 → 内节点（章名/节名）
          - 正文型（title 为空）→ 其 tail 就是自身正文 → 必然产出

        改动④ 祖先链取 `stack[:-1]`，**不含自身**（与旧实现一致）。
        """
        nonlocal pending
        if stack:
            entry = stack[-1]
            content = "\n".join(pending).strip()
            has_own_body = (entry["title"] == "") or bool(content)
            if has_own_body and _should_emit_clause(entry["title"], content):
                ancestors = stack[:-1]
                clauses.append({
                    "clause_no": entry["clause_no"],
                    "title": entry["title"],
                    "content": content,
                    "level": entry["level"],
                    "parent_path": [a["title"] for a in ancestors],
                    "section_path": _build_section_path(ancestors),
                    "is_non_clause": entry["is_non_clause"],
                })
        pending = []

    for line in lines:
        cand = _candidate_of(line)
        if cand is None:
            if not discard_section and line.strip():
                # 非候选行 → 当前条的内容。次分组单元的标题行也走这里，
                # 需剥掉 Markdown 井号前缀，避免标记混进正文。
                pending.append(re.sub(r'^#{1,6}\s*', '', line))
            continue

        flush()                                  # 新候选行到达 → 先结算上一条
        level, clause_no, tail = cand

        is_titled = title_mode[(level, _parent_key(stack, level))]
        title = _clean_title(tail) if is_titled else ""

        if is_filter_non_clause_title(title or tail):
            discard_section = True               # 目次 / Contents：段内一切丢弃
            inherit_non_clause = False
            continue
        discard_section = False

        is_non = (is_non_clause_title(title or tail)
                  or bool(_PAGE_MARKER.match(title or tail))   # 页分隔标记：隐藏但保留（见 _candidate_of）
                  or inherit_non_clause)
        if is_non:
            inherit_non_clause = True

        while stack and stack[-1]["level"] >= level:
            stack.pop()
        stack.append({
            "level": level, "clause_no": clause_no, "title": title,
            "is_non_clause": is_non,
        })
        if not title:
            pending.append(tail)                 # 正文型：编号后文本即正文首行

    flush()                                      # 收尾：结算最后一条
    return clauses
```

> **注意 `title` 与 `tail` 的分工**：标题型行 `title = _clean_title(tail)`（恢复
> 旧行为 `:241`，工程评审 C5）；正文型行 `title = ""` 且 `tail` **不清洗**直接进
> 内容（与旧行为 `:273` 一致）。`_clean_title` 只折叠 2 个以上连续空格，不影响正文语义。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: PASS（含 Task 1–4 的全部用例与既有 34 个用例）

- [ ] **Step 5: pyright + 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "feat: 解析主循环四处改动（R14 投票 / 无条件 flush / 内节点判据 / section_path）

按工程评审 R2 决议保留旧的单遍结构，不引入两遍重写层：
- R14 兄弟多数表决改为预扫，按 (层级, 父键) 分组；平票一律判「无标题」以保内容
- flush 改为**无条件**结算：旧实现只在缓冲非空时结算，导致「标题型且无后续内容」
  的行从未被结算（实测 JGJ107 的 3.0.1 因此从库里消失）
- 内节点判据：无自身正文者只作祖先、不入库（附录A 有自身正文 → 叶条文）
- parent_path / section_path 取 stack[:-1]，**不含自身**；parent_path 是
  classify_clause 的匹配文本输入，语义不得漂移
- 标题型恢复 _clean_title（C5）；正文型 tail 不清洗（与旧行为一致）"
```

## Task 4: 裸编号项不作为条文

**Files:**
- Modify: `app/parser/md_parser.py:113-132`（`_match_clause_line`）
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: 无
- Produces: `_match_clause_line(line: str) -> tuple[int, str, str] | None` — **无点号行一律返回 `None`**

- [ ] **Step 1: 写失败测试**

```python
def test_bare_numbered_item_is_not_a_clause():
    """裸编号项（无点号，如 `1`、`6`）不是条文，是所属条内部的「项」"""
    md = "5.1.1 模板安装应满足下列要求：\n\n1 模板的接缝不应漏浆；\n\n2 接触面应清理干净。\n"
    results = parse_markdown(md)
    nos = [c["clause_no"] for c in results]
    assert nos == ["5.1.1"]          # 裸 1/2 不成条文
    assert "模板的接缝" in results[0]["content"]
    assert "接触面应清理干净" in results[0]["content"]

def test_bare_numbered_short_title_is_still_not_a_clause():
    """短标题形态的裸编号项同样不成条文（旧实现按 ≤20 字会误判为标题型）"""
    md = "5.1.1 一般规定\n\n1 钢筋\n\n2 水泥\n"
    nos = [c["clause_no"] for c in parse_markdown(md)]
    assert "1" not in nos and "2" not in nos

def test_appendix_without_dots_is_still_a_candidate():
    """边界回归：`附录A` 也没有点号，但它是合法结构编号，不得被裸编号项规则误伤。

    判据必须是 `clause_no.isdigit()`，不是 `'.' not in clause_no`。
    """
    md = ("附录A 接头型式检验的加载制度\n\nA.1 检验设备\n\n"
          "A.1.1 加载装置应满足要求。\n")
    results = parse_markdown(md)
    r = [c for c in results if c["clause_no"] == "A.1.1"]
    assert len(r) == 1
    assert any("接头型式检验的加载制度" in p for p in r[0]["parent_path"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "bare_numbered" -v`
Expected: FAIL — `'1' in nos` 为真（旧实现把短标题形态的裸编号判成条文）

- [ ] **Step 3: 实现**

```python
            # 裸阿拉伯数字编号行（如 "1 混凝土结构…" / "6 焊缝外观质量…"）不是条文：
            # 按 R7，它们是次分组单元内部的「项」，归属其所在的条。旧实现用
            # _looks_like_title 按字数猜，导致同一份文档里长项成正文、短项成条文，
            # 判定不一致（实测库里同时存在裸 '1'…'22' 与未被识别的裸项）。
            #
            # ⚠ 判据必须是 `isdigit()` 而**不是** `'.' not in clause_no`：
            # `附录A` 同样没有点号，但它是合法的结构编号，必须继续作为候选行
            # （tests/test_md_parser.py::test_parse_appendix_clauses 断言
            #  `附录A 接头型式检验的加载制度` 出现在后代条文的 parent_path 中）。
            if clause_no.isdigit():
                return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "fix: 裸编号项不再作为条文，归属其所在的条

无点号单数字行（1/6/12…）按 R7 是次分组单元内部的项。旧实现用
_looks_like_title 按字数猜，同一文档里长项成正文、短项成条文，
判定不一致（实测库里同时存在裸 '1'…'22' 与未被识别的裸项）。"
```

---

## Task 5: 已删除 —— 两遍重写层（工程评审 R2 决议，2026-09-27）

> **本 Task 不再实施。** 保留编号以免大面积重编号（历史记录 R1/R2 引用的即此编号）。

**原内容**：把 `parse_markdown` 重写为「第一遍 `_collect_candidates` 收集候选行 →
`_vote_title_mode` 按 `(层级, 父键)` 投票 → 第二遍 `_parse_two_pass` 生成条文」，
并引入 `_RawLine` 数据结构与 `own_body` 回填。

**删除理由（外部复核 codex 逐行核对 + 真实夹具复现，共 6 项缺陷）**：

| 缺陷 | 证据 |
|---|---|
| `flush()` 的 `open_row.own_body` 守卫会丢掉正文型条文 | `own_body` 只统计「本行之后、下一候选行之前」的行，**不含本行自身 tail**。真实夹具实测 content −35,364 字符（−7.9%）、条文 1012→368 |
| `parent_path` / `section_path` 把条文自身算进祖先 | `flush()` 在弹栈**之前**执行 → 结算时栈里仍有该行。与旧行为（`:230`/`:254`/`:267` 均不含自身）不符，且 `parent_path` 是 `classify_clause` 的输入 |
| `is_non_level_group_title` 为其后定义的符号（前向依赖） | R1 把 Task 3 排到 Task 5 之后，而 Task 5 的收集器调用了它 → `NameError` |
| 非条文块的打标保留与目次段过滤机制被整体删除 | 无 `inherit_non_clause` / `discard_section` 等价物 |
| 非 `#` 候选行的 `_clean_title` 丢失 | 旧实现 `:241` 会清洗标题型 tail |
| Task 5 阶段的「全绿检查点」不可能成立 | 该阶段每条候选行都产出条文，`5.1` / `附录A` 会成条文，破坏既有断言 |

**更关键的是**：复核回到旧代码逐行走查后确认，真正修好那 56% 正文归属只需要
两处收窄（无编号标题不再当条文 = Task 1；裸编号项不再当条文 = Task 4）——
非候选行会累积在内容缓冲里、flush 时落到最后一条**真**条文上。因此两遍重写的
增量收益远小于其风险。

**现由 Task 3 承担核心改动**（单遍结构上的四处改动）。

---

## Task 6: 已合并入 Task 3 —— 内节点判据与 `section_path`（工程评审 R2 决议，2026-09-27）

> **本 Task 不再单独实施。** 保留编号以免大面积重编号。

**原内容**：在 `_parse_two_pass` 的基础上增量添加「无自身正文者按内节点处理」
与 `_build_section_path`，并给出 `section_path` 的三条测试。

**合并理由**：内节点判据与 `section_path` 的构建都发生在**同一次主循环改动**内
（`flush()` 的产出条件与祖先链取用），拆成两个 Task 会重现 R1 遇到的分层问题
（前一个 Task 因后一个 Task 的过滤缺失而无法通过自己的检查点）。故两者一并
并入 **Task 3**（该 Task 的 Step 1 组 3 与组 4 即原 Task 6 的测试，已扩充）。

**原 Task 6 的三条测试**均已保留在 Task 3 的 Step 1：
`test_inner_node_without_body_is_not_emitted_but_serves_as_ancestor`、
`test_appendix_with_own_body_is_a_leaf_clause`、
`test_section_path_*` / `test_no_cross_chapter_leak`；
并新增 `test_parent_path_excludes_self`（工程评审 C3）。

---

## Task 7: 非条文块名单按法定名称扩充（R8/R8b）

**Files:**
- Modify: `app/parser/md_parser.py:24-59`（`_NON_CLAUSE_EXACT_TITLES`、`is_non_clause_title`）
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: `_extract_clause_no`（Task 1）
- Produces: `is_non_clause_title(title: str) -> bool`（签名不变，判定集合扩充）

- [ ] **Step 1: 写失败测试**

```python
def test_announcement_and_reference_list_are_non_clause():
    """R8/R8b：公告 / 引用标准名录 是 182 号第六、七条的法定名称

    实测 spec20（CJJ2）有 2 条公告伪条文，引用标准名录的列表项被误吞成
    clause_no='1'/'10'。
    """
    assert is_non_clause_title("公告") is True
    assert is_non_clause_title("引用标准名录") is True

def test_standard_wording_by_legal_name():
    """按法定名称匹配：`标准用词说明` 及其变体 `本规范用词说明` 都要命中。

    实测 CJJ2 写的是「本规范用词说明」，旧实现只覆盖 「本规程用词说明」与
    「本规范用词用语说明」，差「用语」两字就漏判。
    """
    for t in ("标准用词说明", "本规范用词说明", "本规程用词说明", "本规范用词用语说明"):
        assert is_non_clause_title(t) is True, t

def test_legal_name_matching_does_not_over_match():
    """不得误伤含「用词」的正常条文标题"""
    assert is_non_clause_title("用词要求") is False
    assert is_non_clause_title("公告发布要求") is False

def test_non_clause_blocks_are_marked_not_dropped():
    """三者一律打标保留（clause_is_non=1），不整段丢弃（R8/R8b）

    注意与「目次」的区别：目次属**直接过滤**类（见下一条），不进库。
    """
    md = "公告\n\n关于发布行业标准……\n\n1.0.1 正文甲。\n"
    results = parse_markdown(md)
    r = [c for c in results if c["is_non_clause"]]
    assert len(r) == 1 and r[0]["clause_no"] == "公告"
    assert "关于发布行业标准" in r[0]["content"]

def test_toc_section_is_discarded_entirely():
    """目次 / Contents 属「直接过滤」类：段内**一切丢弃**，不进库。

    这是既有行为（`_NON_CLAUSE_FILTER_TITLES` + `discard_section`），本批必须原样保留
    （工程评审 C4：重写若丢掉它，目录行会泄漏进条文正文，且批二 Task 13 的
    tooltip 文案「不含目次」就成了假话）。
    """
    md = "目次\n\n1 总则 ..... 1\n\n2 术语 ..... 3\n\n1.0.1 正文甲。\n"
    results = parse_markdown(md)
    assert all(c["clause_no"] != "目次" for c in results)
    assert all("总则 ..... 1" not in c["content"] for c in results)
    assert any(c["clause_no"] == "1.0.1" for c in results)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "announcement or legal_name or non_clause_blocks" -v`
Expected: FAIL — `is_non_clause_title("公告") is False`

- [ ] **Step 3: 实现**

```python
# R8/R8b（182 号第六、七条）法定名称之外的扩充。
# 三者一律「打标保留」（clause_is_non=1），**不整段丢弃**：
#   - 公告           实测 spec20 有 2 条伪条文
#   - 引用标准名录    列表项被误吞成 clause_no='1'/'10'
#   - 标准用词说明    旧实现只覆盖 `本规程用词说明` 与 `本规范用词用语说明`，
#                    而 CJJ2 写的是 `本规范用词说明`（差「用语」两字 → 漏判）
_NON_CLAUSE_EXACT_TITLES = {
    "前言", "目次", "Contents", "条文说明",
    "公告", "引用标准名录", "标准用词说明",
}

# 用词说明类：按「法定名称」匹配，再兼容前后缀变体
_WORDING_DOC_SUFFIX = "用词说明"


def is_non_clause_title(title) -> bool:
    """判断标题（或条文号）是否为非条文块。

    规则（精确命中 + 受控的变体兜底）：
    - 精确命中 `_NON_CLAUSE_EXACT_TITLES`
    - 以「前言」开头
    - 含「条文说明」
    - **以「用词说明」结尾**（覆盖 `标准用词说明` / `本规范用词说明` /
      `本规程用词说明` / `本规范用词用语说明` 全部变体；用结尾匹配避免
      误伤「用词要求」这类真实标题）
    """
    t = (title or "").strip()
    if not t:
        return False
    if t in _NON_CLAUSE_EXACT_TITLES:
        return True
    if t.startswith("前言"):
        return True
    if "条文说明" in t:
        return True
    if t.endswith(_WORDING_DOC_SUFFIX):
        return True
    return False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "feat: 非条文块名单按 182 号法定名称扩充（公告/引用标准名录/用词说明）

补公告与引用标准名录（实测 CJJ2 各有伪条文/列表项误吞）；用词说明改为
按法定名称结尾匹配，修掉 CJJ2「本规范用词说明」因差「用语」两字而漏判。
三者一律打标保留，不整段丢弃。"
```

---

## Task 8: 固定夹具 + 结构勘察脚本（三项回归指标 + 面包屑覆盖率）

**Files:**
- Create: `tests/fixtures/cjj2_source.md`
- Create: `scripts/survey_structure.py`
- Test: `tests/test_md_parser.py`（追加一条夹具可用性断言）

**Interfaces:**
- Consumes: `parse_markdown`（Task 1–7）
- Produces:
  - `survey_structure(md_text: str) -> dict` — 键：`clause_count`, `content_chars`, `fake_clause_no_count`, `breadcrumb_coverage`, `missing_sections: list[str]`
  - CLI：`python scripts/survey_structure.py <md路径> [--json]`

- [ ] **Step 1: 固化夹具并写断言**

```bash
mkdir -p tests/fixtures
cp "data/outputs/f543577f/f543577f.md" tests/fixtures/cjj2_source.md
```

```python
# tests/test_md_parser.py 追加
from pathlib import Path
import pytest

CJJ2_FIXTURE = Path(__file__).parent / "fixtures" / "cjj2_source.md"

@pytest.fixture
def cjj2_md() -> str:
    """CJJ2 源 md 的固定副本。

    不依赖 data/outputs/（运行时目录，且历史上 data/uploads/ 已被 pytest 污染）。
    """
    return CJJ2_FIXTURE.read_text(encoding="utf-8")

def test_cjj2_fixture_is_available(cjj2_md):
    assert len(cjj2_md) > 400_000
```

- [ ] **Step 2: 写勘察脚本的失败测试**

```python
# tests/test_md_parser.py 追加
def test_expected_ancestor_count_excludes_zero_segments():
    """R3：`X.0.Y` 没有 `X.0` 这一级，故 `3.0.1` 应有 1 级祖先而非 2 级。

    若按 `level - 1` 判完整性，所有 `X.0.Y` 条文会被系统性误判为面包屑不完整
    （实测占 JGJ107 的 53%），进而让覆盖率被大幅低估、误导 Task 3 的去留。
    """
    from scripts.survey_structure import _expected_ancestor_count
    assert _expected_ancestor_count("3.0.1") == 1
    assert _expected_ancestor_count("1.0.2") == 1
    assert _expected_ancestor_count("6.1.1") == 2
    assert _expected_ancestor_count("6.1") == 1
    assert _expected_ancestor_count("6") == 0


def test_survey_reports_breadcrumb_coverage(cjj2_md):
    """批一验收指标之一：面包屑覆盖率。

    用于量化「Task 3（目次对齐）是否还需要」——修完层级后若仍有节的标题
    缺失，其下条文的 section_path 会缺段，覆盖率会掉下来。
    """
    from scripts.survey_structure import survey_structure
    stats = survey_structure(cjj2_md)
    assert stats["clause_count"] > 900
    assert 0.0 <= stats["breadcrumb_coverage"] <= 1.0
    assert isinstance(stats["missing_sections"], list)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "fixture_is_available or survey_reports" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.survey_structure'`

- [ ] **Step 4: 实现勘察脚本**

```python
"""结构勘察：批次验收的回归门禁。

产出五项指标（批一验收用，见 spec §6 与 CEO 评审记录 Section 6）：
  1. clause_count           条文数
  2. content_chars          全部 content 的字符总数（**含 PaddleOCR-VL 标记的原始口径**）
  3. content_chars_plain    同上，但经 `plain_text` 归一（**Task 9 守恒断言的基线口径**）
  4. fake_clause_no_count   不含数字的条文号个数（伪条文号）
  5. breadcrumb_coverage    section_path 段数 == 应有祖先数（按 R3 剔除 0 段）的条文占比
  6. missing_sections       被引用却找不到标题的节号（量目次对齐的残余缺口）

用法：python scripts/survey_structure.py <md路径> [--json]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.text_clean import plain_text  # noqa: E402
from app.parser.md_parser import parse_markdown  # noqa: E402


def _expected_ancestor_count(clause_no: str) -> int:
    """按 R3 计算该条文**应有**多少级祖先（即面包屑应有的段数）。

    `X.0.Y`（章内不分节）没有 `X.0` 这一级，故 `3.0.1` 应有 1 级祖先（`3`）。
    **不能**用 `level - 1`：`3.0.1` 的层级是 3，`level-1=2` ≠ 实际 1 级，
    会把所有 `X.0.Y` 条文系统性误判为「面包屑不完整」——实测这类条文占
    JGJ107 的 53%（39/73）、CJJ2 的 44 条，足以让覆盖率被大幅低估，
    进而错误地让 Task 3（目次对齐）看起来有必要（D6.2 正是用本指标决策）。
    """
    segs = clause_no.split('.')[:-1]          # 去掉末段（条号）
    return sum(1 for s in segs if s != '0')   # R3：0 段不构成节点


def survey_structure(md_text: str) -> dict:
    clauses = parse_markdown(md_text)
    fake = [c for c in clauses if not re.search(r'\d', c["clause_no"])]

    complete = 0
    for c in clauses:
        segs = [s for s in (c.get("section_path") or "").split(" > ") if s]
        if segs and len(segs) == _expected_ancestor_count(c["clause_no"]):
            complete += 1
    coverage = complete / len(clauses) if clauses else 0.0

    # 残余缺口：条文编号的上一级节点号（如 18.3.1 → 18.3）在祖先链里找不到。
    # ⚠ R3：`X.0.Y` 的上一级是 `X.0`，而它按设计**不存在**（0 段不构成节点）
    #   → 必须跳过，否则 `3.0` / `1.0` / `2.0` 会被系统性报成「缺失的节」，
    #   与覆盖率指标一样夸大残余缺口、误导 Task 3 的去留判断（D6.2）。
    present = {c["clause_no"] for c in clauses}
    missing: set[str] = set()
    for c in clauses:
        parts = c["clause_no"].split('.')
        if len(parts) < 2:
            continue
        parent_no = ".".join(parts[:-1])
        if parts[-2] == '0':          # R3：节位为 0 → 该级本就不存在
            continue
        if parent_no in present:
            continue
        if not any(f"{parent_no} " in s for s in (c.get("section_path") or "").split(" > ")):
            missing.add(parent_no)

    return {
        "clause_count": len(clauses),
        "content_chars": sum(len(c["content"]) for c in clauses),
        # ⚠ SDD 实施前扫描 F2：Task 9 的守恒断言口径是 `plain_text` 归一后的字符数
        # （避免 PaddleOCR-VL 标记残留导致自然波动），而上面的 `content_chars` 是
        # **含标记的原始口径**——两者不可混用。故本脚本必须同时输出归一口径，
        # 供 Task 9 直接取基线值。
        "content_chars_plain": sum(len(plain_text(c["content"])) for c in clauses),
        "fake_clause_no_count": len(fake),
        "breadcrumb_coverage": round(coverage, 4),
        "missing_sections": sorted(missing),
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    if not args:
        print(__doc__)
        return 2
    md = Path(args[0]).read_text(encoding="utf-8")
    stats = survey_structure(md)
    if "--json" in sys.argv:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        for k, v in stats.items():
            if k == "missing_sections":
                print(f"{k}: {len(v)} 个 -> {v[:20]}")
            else:
                print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑测试确认通过并输出真实基线**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "survey" -v`
Run: `D:/Python/python.exe scripts/survey_structure.py tests/fixtures/cjj2_source.md`
Expected: PASS；**把输出的五项指标记入下方「批一验收基线」表**（这是后续 Task 的对照值）

- [ ] **Step 6: 提交**

```bash
git add tests/fixtures/cjj2_source.md scripts/survey_structure.py tests/test_md_parser.py
git commit -m "feat: 结构勘察脚本（五项回归指标）+ CJJ2 固定夹具

夹具拷入 tests/fixtures/，不再依赖运行时目录 data/outputs/。
勘察脚本产出 clause_count / content_chars / fake_clause_no_count /
breadcrumb_coverage / missing_sections，作为批一验收门禁。"
```

### 批一验收基线

> Task 8 Step 5 首次运行后**立即填入真实值**，后续 Task 以此为对照。未填不得进入 Task 9。

| 指标 | Task 8 实测（改造前基线） | 批一目标 |
|---|---|---|
| `clause_count` | 待填 | 不再含裸编号 / 次分组单元 / 年份等伪条文 |
| `content_chars` | 待填（旧实现 444,976，**含标记口径**） | 参考值；不作断言 |
| **`content_chars_plain`** | 待填（`plain_text` 归一口径） | **不得减少**（守恒）——**Task 9 的 `BASELINE_PLAIN_CHARS` 取此值** |
| `fake_clause_no_count` | 待填（旧实现 124） | **明显下降**（目标 ≤ 12） |
| `breadcrumb_coverage` | 待填 | 提升；缺口清单用于决定目次对齐（Task 3）去留 |
| `missing_sections` | 待填 | 作为 Task 3 是否启用的证据 |

---

## Task 9: 守恒断言（含变异验证）

> **本 Task 是 CEO 评审 CRITICAL-1 的唯一防线**：旧实现下 124 条伪条文号持有
> 252,514 字符（占 56%）。若改造让伪条文号消失而内容一并丢失，本条断言必须失败。

**Files:**
- Create: `tests/test_parse_conservation.py`
- Test: 自身

**Interfaces:**
- Consumes: `parse_markdown`、`survey_structure`（Task 8）
- Produces: 无新生产接口

- [ ] **Step 1: 写守恒断言**

```python
"""守恒断言：解析改造不得让正文凭空消失。

背景（CEO 评审 CRITICAL-1）：旧实现下「不含数字的条文号」124 条持有
252,514 字符，占全部 content 的 56%。若改造让这些伪条文号不再出现，
而它们的正文没有回流到真实的条上，就是 56% 语料静默蒸发。

**唯一能拦住它的断言**：content 总字符数不减少。

实现陷阱（必须遵守）：content 含着 PaddleOCR-VL 的 HTML/LaTeX 标记，
若解析过程对标记的保留程度发生变化，字符数会自然波动。故断言统一基于
`plain_text()` 归一后的正文长度（与向量/FTS 消费方口径一致）。
"""
from pathlib import Path

import pytest

from app.ai.text_clean import plain_text
from app.parser.md_parser import parse_markdown

CJJ2_FIXTURE = Path(__file__).parent / "fixtures" / "cjj2_source.md"

# 改造前的实测基线（旧实现，plain_text 归一口径）——由 Task 8 Step 5 填入
BASELINE_PLAIN_CHARS = 0  # ← 待填


@pytest.fixture
def cjj2_md() -> str:
    return CJJ2_FIXTURE.read_text(encoding="utf-8")


def test_content_is_conserved(cjj2_md):
    """正文总量不得减少（允许微小增长：次分组单元标签等文本并入 content）"""
    clauses = parse_markdown(cjj2_md)
    total = sum(len(plain_text(c["content"])) for c in clauses)
    assert BASELINE_PLAIN_CHARS > 0, "先由 Task 8 Step 5 填入基线"
    assert total >= BASELINE_PLAIN_CHARS, (
        f"正文总量从 {BASELINE_PLAIN_CHARS} 降到 {total}——"
        f"有内容随伪条文号一并丢失（CRITICAL-1）"
    )


def test_no_fake_clause_no_carries_bulk_content(cjj2_md):
    """伪条文号不得再持有大块正文（旧的 一般项目 单条吞 9,795 字符）"""
    import re
    clauses = parse_markdown(cjj2_md)
    fake_big = [c for c in clauses
                if not re.search(r'\d', c["clause_no"])
                and len(plain_text(c["content"])) > 2_000]
    assert not fake_big, f"仍有伪条文号持有超 2000 字符正文: {[(c['clause_no'], len(c['content'])) for c in fake_big]}"
```

- [ ] **Step 2: 跑测试，确认它现在**通过**（改造已完成），并填入基线**

Run: `D:/Python/python.exe -m pytest tests/test_parse_conservation.py -v`
Expected: PASS（若 FAIL，说明 Task 3–6 丢了内容，**必须先修再继续**）

- [ ] **Step 3: 变异验证（证明断言可失败——不得省略）**

变异体取**工程评审 SC-1 实测过的真实缺陷**：把 Task 2 的 `_is_zero_segment_node`
判据放宽回「任意段为 0」，于是所有 `X.0.Y` 条文被跳过（实测 JGJ107 丢 39 条 /
44,353 字符、CJJ2 丢 44 条 / 12,796 字符）。这条变异精确代表「内容被吞」这一类
缺陷，且是一行可复现的改动。

```bash
# 1) 先确认基线通过
D:/Python/python.exe -m pytest tests/test_parse_conservation.py -v
# Expected: PASS

# 2) 施加变异（一行）：把末段判据放宽为「任意段为 0」
D:/Python/python.exe - <<'PY'
import re, pathlib
p = pathlib.Path("app/parser/md_parser.py")
src = p.read_text(encoding="utf-8")
old = "    return len(parts) > 1 and parts[-1] == '0'"
new = "    return len(parts) > 1 and '0' in parts   # MUTANT"
assert src.count(old) == 1, "变异目标行未唯一命中，先核对源码形态"
p.write_text(src.replace(old, new), encoding="utf-8")
print("变异已施加")
PY

# 3) 断言必须失败，且报出字符数下降
D:/Python/python.exe -m pytest tests/test_parse_conservation.py -v
# Expected: FAIL — 报错文案形如「正文总量从 N 降到 M——有内容随伪条文号一并丢失」

# 4) 立即还原（本步骤结束后必须回到干净状态）
git checkout -- app/parser/md_parser.py
D:/Python/python.exe -m pytest tests/test_parse_conservation.py -v
# Expected: PASS（确认还原成功，不得留下变异体）
```

**判据**：第 3 步若仍然 PASS，说明守恒断言无效——**必须先修断言再继续，不得进入 Task 10**。

- [ ] **Step 4: 提交**

```bash
git add tests/test_parse_conservation.py
git commit -m "test: 新增正文守恒断言（CRITICAL-1 的唯一防线）

旧实现下 124 条伪条文号持有 252,514 字符（占 content 56%）。断言基于
plain_text 归一后的字符总数（避免标记残留导致自然波动），并做了变异验证：
模拟内容丢失时断言必须失败。"
```

---

## Task 10: 分类标签分布回归基线

> CEO 评审 CRITICAL-2：`parent_path`是分类引擎的匹配文本的一部分
> （`import_routes.py:469` → `rule_engine.py:66`）。改层级必然改变分类输入。

**Files:**
- Create: `tests/test_classify_baseline.py`
- Test: 自身

**Interfaces:**
- Consumes: `parse_markdown`、`classify_clause`、`app.database` 的规则表
- Produces: 无新生产接口

- [ ] **Step 1: 写基线测试**

```python
"""分类标签分布回归：改 parent_path 会让 dim 得分与标签漂移。

`rule_engine.classify_clause(clause_text, parent_path, rules)` 把祖先标题
拼进 `augmented_text` 参与规则匹配，因此解析改造会改变分类结果。本测试冻结
「对同一批固定条文 + 固定规则集，分类结果与既有基线一致」，使漂移可被发现。

**隔离要求**（历史 learning 9/10）：本仓 app/database.py 直接读模块级常量，
无环境变量入口，必须 monkeypatch DATABASE_PATH。
"""
import pytest

from app.classifier.rule_engine import classify_clause
from app.parser.md_parser import parse_markdown

RULES = [
    {"id": 1, "dimension": "dim4", "pattern": "钢筋", "match_type": "keyword",
     "priority": 0, "threshold": 0.6, "is_active": 1},
    {"id": 2, "dimension": "dim4", "pattern": "模板", "match_type": "keyword",
     "priority": 0, "threshold": 0.6, "is_active": 1},
]

MD = """## 6 混凝土分项工程

### 6.1 模板

#### 6.1.1 一般规定

支架应根据工程结构形式进行设计。

### 6.2 钢筋

#### 6.2.1 原材料

进场时应抽取试件作屈服强度检验。
"""

# ⚠ 工程评审 C8 修正：上述正文**刻意不含**「模板」「钢筋」。
# 初稿的正文含「模板及其支架…」「钢筋进场时…」，于是 dim4 命中来自**正文**，
# 该用例在改造前就已通过——它冻结的是一个与本次改动无关的玩具分布，
# 真实漂移（来自 parent_path 变化）根本不会被发现。
# 现在命中只可能来自祖先标题，耦合才真正被隔离。


def _labels(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in parse_markdown(md):
        _scores, best, _ids = classify_clause(c["content"], c.get("parent_path", []),
                                              RULES, synonyms=[])
        out[c["clause_no"]] = best.get("dim4", "")
    return out


def test_classification_inputs_include_ancestor_titles():
    """固定事实：祖先标题**确实**进入分类匹配文本（这是耦合的机制依据）。

    正文既不含「模板」也不含「钢筋」，标签只能来自 `parent_path`——
    若 `parent_path` 的构成变了（如含自身、或丢了祖先），本用例必须失败。
    """
    labels = _labels(MD)
    assert labels["6.1.1"] == "模板"
    assert labels["6.2.1"] == "钢筋"


def test_label_disappears_when_ancestor_chain_is_broken():
    """反向断言：故意传空 `parent_path` 时标签必须消失。

    这条**证明**上面的命中确实来自祖先标题，而不是正文的偶然包含——
    没有它，`test_classification_inputs_include_ancestor_titles` 无法自证隔离。
    """
    md_clauses = parse_markdown(MD)
    c = next(x for x in md_clauses if x["clause_no"] == "6.1.1")
    _scores, best, _ids = classify_clause(c["content"], [], RULES, synonyms=[])
    assert best.get("dim4", "") != "模板"


def test_classification_labels_stable_against_baseline():
    """基线：与改造后实测一致。**改造若不改变本用例，说明层级未影响分类**；
    若改变，必须在此显式更新并说明理由（不得静默改）。"""
    assert _labels(MD) == {
        "6.1.1": "模板",
        "6.2.1": "钢筋",
    }
```

- [ ] **Step 2: 跑测试，把实测值冻结为基线**

> **本 Task 不是「先红后绿」的 TDD**（工程评审 C8 修正后明确）：它是一条**基线冻结**
> （characterization）测试。三个用例都是对**已批准行为**的断言，不是待实现的新行为，
> 所以「先失败」不适用。它的价值在于：日后 `parent_path` 的构成再变时立刻报警。

Run: `D:/Python/python.exe -m pytest tests/test_classify_baseline.py -v`
Expected: **PASS**（Task 1–4 落地后，`parent_path` 已恢复「不含自身」的正确语义）

若**失败**，停下来判断，不要径直改断言：
- `test_classification_inputs_include_ancestor_titles` 失败 → `parent_path` 没进 `classify_clause`，
  或祖先链丢了 —— **真回归**，须修实现。
- `test_label_disappears_when_ancestor_chain_is_broken` 失败 → 说明命中的来源不是祖先标题，
  样例需要重做（C8 的隔离没做到）。
- `test_classification_labels_stable_against_baseline` 失败 → 冻结值与实际不符，见 Step 3。

- [ ] **Step 3: 冻结实测值并记录差异**

把 Step 2 实际得到的标签写入 `test_classification_labels_stable_against_baseline`，**并在提交信息中
列出实测值与初稿预设值（`6.1.1→模板`、`6.2.1→钢筋`）是否一致**。若不一致，必须解释原因
（是 C8 的样例修正所致，还是 `parent_path` 语义仍有偏差）。

- [ ] **Step 4: 全量回归确认无其它漂移**

Run: `D:/Python/python.exe -m pytest tests/ -v`
Expected: PASS（本 Task 触及 `parse_markdown` 的输出，属大范围影响面，跑全量）

- [ ] **Step 5: 提交**

```bash
git add tests/test_classify_baseline.py
git commit -m "test: 冻结分类标签分布基线（CRITICAL-2）

classify_clause 把 parent_path 拼进匹配文本，故层级改造会改变 dim 标签。
本测试冻结固定条文 + 固定规则集下的标签分布，使漂移可见。
提交信息列出标签变化的条文及原因。"
```

---

## Task 11: 重复条文号诊断

> CEO 评审实测：**131 个条文号重复、涉及 388 行（占解析结果 38%）**，其中仅 2 行
> `is_non_clause=1`。R14 的兄弟组按 `(层级, 父键)` 分组，重复条文号会使组定义失效。

**Files:**
- Modify: `scripts/survey_structure.py`（追加重复诊断）
- Test: `tests/test_md_parser.py`（追加一条）

**Interfaces:**
- Consumes: `parse_markdown`
- Produces: `survey_structure` 返回值新增 `duplicate_clause_no: dict[str, int]`

- [ ] **Step 1: 写失败测试**

```python
def test_survey_reports_duplicates(cjj2_md):
    """重复条文号必须被量化：它们会让 R14 的兄弟组定义失效"""
    from scripts.survey_structure import survey_structure
    stats = survey_structure(cjj2_md)
    dups = stats["duplicate_clause_no"]
    assert isinstance(dups, dict)
    assert all(v > 1 for v in dups.values())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "duplicates" -v`
Expected: FAIL — `KeyError: 'duplicate_clause_no'`

- [ ] **Step 3: 实现（并解释 388 行的来源）**

```python
    from collections import Counter
    counts = Counter(c["clause_no"] for c in clauses)
    duplicate_clause_no = {k: v for k, v in counts.items() if v > 1}
```

`survey_structure` 的 docstring 追加第 6 项指标。**并在脚本的文本输出里按
`is_non_clause` 拆分计数**，用于回答「重复是否合法（正文 / 条文说明 各一份）」：

```python
    dup_rows = [c for c in clauses if counts[c["clause_no"]] > 1]
    non = sum(1 for c in dup_rows if c["is_non_clause"])
    stats["duplicate_rows"] = len(dup_rows)
    stats["duplicate_rows_is_non"] = non
```

> **⚠️ 工程评审修正 SC-3（P1，改 Task 8 的覆盖率判据）**：初稿的完整性判据是
> `len(segs) == c["level"] - 1`，它把 **R3 的 `X.0.Y` 系统性误判为不完整**：
> `3.0.1` 的层级是 3（两个点），但它的祖先只有 `3` 一节（不存在 `3.0`），
> 实际段数 1 ≠ 2 → 被计为「不完整」。实测这类条文占 **JGJ107 的 53%（39/73）**。
>
> 后果不只是数字难看：`breadcrumb_coverage` 与 `missing_sections` 正是 **D6.2
> 用来决定 Task 3（目次对齐）去留的证据**。判据偏低会让覆盖率被大幅低估，
> 从而**错误地**让目次对齐看起来有必要。
>
> 正确判据：期望段数 = 「`clause_no` 去掉末段、再剔除所有 `0` 段」后的段数。
> Task 8 Step 4 的实现改为：

```python
def _expected_ancestor_count(clause_no: str) -> int:
    """按 R3 计算该条文**应有**多少级祖先（面包屑段数）。

    `X.0.Y`（章内不分节）没有 `X.0` 这一级，故 `3.0.1` 应有 1 级祖先（`3`）。
    直接用 `level - 1` 会把它算成 2 级 → 系统性误判为不完整。
    """
    segs = clause_no.split('.')[:-1]          # 去掉末段（条号）
    return sum(1 for s in segs if s != '0')   # R3：0 段不构成节点
```

```python
    complete = 0
    for c in clauses:
        segs = [s for s in (c.get("section_path") or "").split(" > ") if s]
        if segs and len(segs) == _expected_ancestor_count(c["clause_no"]):
            complete += 1
    coverage = complete / len(clauses) if clauses else 0.0
```

Step 2 的测试相应增加一条：`_expected_ancestor_count("3.0.1") == 1`、
`_expected_ancestor_count("6.1.1") == 2`、`_expected_ancestor_count("1.0.2") == 1`。

- [ ] **Step 4: 跑测试 + 输出真实诊断**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "duplicates" -v`
Run: `D:/Python/python.exe scripts/survey_structure.py tests/fixtures/cjj2_source.md`
Expected: PASS；把 `duplicate_rows` / `duplicate_rows_is_non` 记入批一验收基线表

- [ ] **Step 5: 判定与处置**

按实测结果二选一，并**把结论写进提交信息**：

- **若重复集中在 `is_non_clause=1`（条文说明与正文重号）** → 合法，记为已知事实；
  在 R14 分组键中加入 `is_non_clause` 以隔离两组：
  `groups.setdefault((r.level, r.parent_key, r.is_non), []).append(r)`
- **若重复出现在正常条文池** → 是缺陷。在 `survey_structure` 输出里列出前 20 个
  重复号与原行号，作为批一验收的必答项（不在此 Task 修，避免范围蔓延）。

- [ ] **Step 6: 提交**

```bash
git add scripts/survey_structure.py tests/test_md_parser.py
git commit -m "feat: 重复条文号诊断 + R14 分组键按需隔离

实测 131 个条文号重复、涉及 388 行，仅 2 行 is_non_clause=1。
重复会让 R14 的 (层级,父键) 兄弟组定义失效。按实测结果决定是否在分组键
中加入 is_non_clause（结论见提交信息）。"
```

---

## Task 12: 批一验收（重导 CJJ2 + 五项指标对照）

**Files:**
- 无新增（验收与重导）

**Interfaces:**
- Consumes: 全部 Task 1–11 的产物
- Produces: 验收结论（写入本计划文件）

- [ ] **Step 1: 全量测试（本批属大范围改动，跑全量）**

Run: `D:/Python/python.exe -m pytest tests/ -v`
Expected: PASS（若失败，先判断是「测旧尺子的断言」还是真回归）

- [ ] **Step 2: pyright 全仓 0 error**

Run: `cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pyright`
Expected: `0 errors`（注意：**编辑器诊断与 CLI 冲突时以仓库根 CLI pyright 为准**）

- [ ] **Step 3: 五项指标对照**

Run: `D:/Python/python.exe scripts/survey_structure.py tests/fixtures/cjj2_source.md`
逐项对照「批一验收基线」表，填写实际值。**任一指标退化即视为验收失败。**

- [ ] **Step 4: 重启服务（严格按项目 CLAUDE.md §三）**

```bash
netstat -ano | grep :8000 | grep LISTENING        # 列出所有监听 PID
wmic process where "name='python.exe'" get ProcessId,CommandLine | grep multiprocessing
# reloader + worker 全部显式 taskkill //F //PID <pid>（逐条执行，观察输出）
netstat -ano | grep :8000 | grep LISTENING        # 应无输出
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
netstat -ano | grep :8000 | grep LISTENING        # 应恰好 1 个
```

- [ ] **Step 5: 重导 CJJ2 并核对**

在维护页重导 `CJJ 2-2008`（spec_id=20），随后核对：
1. 条文数、`content_chars` 与勘察脚本一致
2. `fake_clause_no_count` 明显下降
3. 详情弹窗能打开 `14.3.1` 且其正文**包含**主控项目/一般项目下的项与检查数量
4. 维护宫格无红点

- [ ] **Step 6: 记录验收结论并提交**

```bash
git add docs/superpowers/plans/2026-09-27-batch1-parser-hierarchy.md
git commit -m "docs: 批一验收结论（五项指标实测对照）

重导 CJJ2 后填写实测值；退化项与未达标项在此显式记录。"
```

---

## Decision ledger

> 本节的记录由 `/plan-eng-review`（2026-09-27，目标 = 本文件）写入。
> 四项**事实性修正**（SC-1~SC-4）已直接落到对应 Task 的正文，按决策程序
> 「更正与行为不符的陈述无需另开决策」处理，记录如下备查：

| ID | 严重度 | 置信 | 位置 | 修正 |
|---|---|---|---|---|
| SC-1 | P0 | 9/10 | Task 2 Step 3 | `_has_zero_segment` 判据由「任意段为 0」收窄为**末段为 0**，并改名 `_is_zero_segment_node`；否则 `X.0.Y` 条文整体不入库（实测 JGJ107 39 条 / 44,353 字符 = 80%、CJJ2 44 条 / 12,796 字符），且与 Task 2 自己的测试直接矛盾 |
| SC-2 | P1 | 9/10 | Task 1 Step 3 | `_NUM_PATTERNS` 改形态后**三处**解包点只有一处被改；补 `:113`（`_match_clause_line`）与 `:299`（`_extract_title`），并加源码断言 |
| SC-3 | P1 | 8/10 | Task 8 Step 4 | 覆盖率与残余缺口判据对 R3 的 `X.0.Y` 系统性误判（`3.0.1` 实有 1 级祖先却按 2 级判）；新增 `_expected_ancestor_count` 并按 `0` 段剔除；否则会**错误地**让 Task 3 显得有必要（D6.2 正是用该指标决策） |
| SC-4 | P1 | 9/10 | Task 9 Step 3 | 变异指令不可执行且引用了 Task 4 已改掉的代码形态；改为对 `_is_zero_segment_node` 施加**一行可复现变异**（精确复现 SC-1 的缺陷），并含还原与复验步骤 |
| SC-6 | P3 | 6/10（需核实） | Task 5 | `_vote_title_mode` 平票回退 `members[0]`：2 条组 1:1 时若 members[0] 像标题，则两者都判标题型；若 members[1] 无自身正文，它会按内节点被丢弃（其 tail 文本消失）。建议平票**偏向「无标题」**（保内容）。此项随 R1 的选项一并裁定 |

### R1: Task 5/6 的第二遍实现方式与 Task 3 的返工

Finding: SC-5 — [P1] (confidence: 8/10) `docs/superpowers/plans/2026-09-27-batch1-parser-hierarchy.md` Task 5 Step 3 / Task 6 Step 3 — 计划只给出了第一遍的两个辅助函数与「第二遍生成条文（同一次改写中完成）」这句散文，**没有第二遍的代码**；Task 6 Step 3 仅一行片段（`"section_path": _build_section_path(ancestor_stack)`）。而 R7 的「次分组单元内容回流」决定 252,514 字符（占 content 总字符 56%）的归属，是本计划最要害的一段。且 Task 3 的生产代码（改 `parse_markdown` 的 `#` 路径与编号行路径）在 Task 5 会被整体替换。

Plan baseline: 三处均为 Task 5 Step 3 的散文描述，无代码；Task 3 先于 Task 5 实现同一条代码路径。
Runtime evidence: 计划文本本身；SC-1 实测确认 `X.0.Y` 归属是真实大规模问题；Task 2 的测试与 Task 5 的代码相互矛盾（已修正）。

| Commitment | Current | A | B | C |
|---|---|---|---|---|
| Task 5 第二遍代码 | 无（散文） | **补齐完整代码** | 无（并入合并任务） | 补齐完整代码 |
| Task 3 的生产代码 | 先写后删（被 Task 5 替换） | **改为在 Task 5 之后增量添加** | 与 Task 5/6 合并为一次写成 | 保留先写后删 |
| Task 6 的内节点判据 | 一行片段 | **改为在 Task 5 之后增量添加** | 同上合并 | 保留片段 |
| 任务划分 | 12 Task | **12 Task（顺序调整）** | 11 Task（3/5/6 合一） | 12 Task（不变） |
| 平票规则（SC-6） | `members[0]` | **偏向「无标题」（保内容）** | 同 A | 维持 `members[0]` |
| 可独立否决的粒度 | 每行为一个 Task | **保留** | 丧失（三行为不可分） | 保留 |

### R1

Question D1:

D1 — 第二遍的代码与 Task 3 的返工怎么处理？

Project/branch/task: `main` 分支，批一实施方案（解析器层级重建）的 Task 5/6 实现方式。

ELI10：这份计划最要紧的一段是「重写解析主循环」——那 252,514 字符（占全部正文 56%）该挂到哪个条上，全靠它。但计划里这段只有一句散文「第二遍生成条文（同一次改写中完成）」，没有代码；而它前面那个 Task 3 已经把同一段代码改过一遍，等 Task 5 重写时又要被删掉重写。所以现在要么先把代码补出来并把顺序理顺，要么干脆把这几件事合成一件一次做成。

Stakes if we pick wrong: 选 C 的话实现者要自己设计那 56% 字符的归属逻辑，而这正是整个批一的目的；选 B 则三个可独立评审的行为被塞进一个不可分否决的任务里，出错时无法只退回其中一处。

Recommendation: A，因为它在消除返工与「要害机制无代码」的同时，保住了每个行为可独立提交、可独立否决的粒度——符合「显式优于聪明」与「最小清晰改动」。

Completeness: A=10/10, B=8/10, C=6/10

Pros / cons:
A) 顺序调整 + 补齐第二遍代码（推荐）
  ✅ Task 5 给出第二遍完整代码；Task 3（R7 次分组单元）与 Task 6（内节点判据）改为在 Task 5 之后**增量添加**到该代码上，不再先写后删
  ✅ 每个行为仍是独立 Task 与独立提交，出错时能只退回其中一处；平票规则一并定为「偏向无标题」以保内容
  ❌ 要改动三个 Task 的编号与依赖说明（文档返工）
  ❌ Task 5 单步代码量变大，评审时要读更长的一段
B) 合并为一个「重写 parse_markdown」任务
  ✅ 最少返工：一次写成为止，没有「先写一遍再删」的浪费
  ✅ 三段测试可同时先写（红）再一起转绿，是最纯粹的 TDD 形态
  ❌ 三个行为（R7 回流 / 内节点判据 / section_path）塞进一个任务，无法只否决其中一处
  ❌ 单任务跨度大，提交粒度粗，中途出错难回溯
C) 维持现状，只补齐 Task 5 的代码
  ✅ 改动最小，只动一个 Task，其余编号与依赖说明不变
  ❌ 留下 Task 3 的返工（同一段代码写两遍、第一遍随后被删）
  ❌ 平票规则维持回退 `members[0]`，2 条组 1:1 时可能丢掉无正文条文的 tail 文本

Net: 真正权衡的是「改写文档换取顺直的实现路径与可独立否决的粒度」对「不写文档但留一处返工」。

Header: 第二遍实现方式

Options:
A) 顺序调整 + 补齐第二遍代码
补齐 Task 5 的第二遍完整代码；Task 3 与 Task 6 改为在 Task 5 之后增量添加到该代码上（不再先写后删）；平票规则一并定为**偏向「无标题」以保内容**（SC-6）。12 个 Task，每个行为可独立提交与独立否决。
B) 合并为一个重写任务
把 Task 3/5/6 合并成单个「重写 parse_markdown」任务，三段测试先写齐再一次实现到位；平票规则同样定为**偏向「无标题」**（SC-6）。返工最少，但三个行为不可分，提交粒度最粗。
C) 维持现状，只补 Task 5 代码
只给 Task 5 补上第二遍代码，Task 3 的返工保留；平票规则**维持回退 `members[0]`**（SC-6 不修）。改动最小，但同一段代码写两遍、且平票时可能丢掉无正文条文的 tail 文本。

State: approved
Actual answer: A) 顺序调整 + 补齐第二遍代码（用户于本会话选定，2026-09-27）
Accepted scope: Task 5 补齐第二遍完整代码（`_parse_two_pass`）；`_vote_title_mode` 平票分支改为一律判「无标题」（SC-6 一并采纳）；新增「执行顺序」节，把 Task 4 提到 Task 5 之前、Task 3 与 Task 6 移到 Task 5 之后并改为增量添加；Task 3 与 Task 6 各加执行时点说明。**未包含**：Task 编号重排（保持 1–12 不变以缩小 diff）、Task 3/5/6 合并。
History: 初稿为「Task 5 Step 3 散文 + Task 3 先写后删」；R1 于本会话提交并按决策程序记录、Read-back 验证后询问，用户选 A。

### R2: 两遍重写是否保留，还是回到「单遍 + 收窄 + 预算投票」

Finding: C1–C7（外部复核 codex 完成，逐行核对 + 在真实 CJJ2 夹具上复现）— 两遍重写引入 6 项缺陷，其中 C2 与 C3 **与本计划自己的测试**直接矛盾（`test_body_type_clause_is_emitted_even_without_following_lines` 期望正文型无后续行的条文产出，而 `flush()` 的 `own_body` 守卫会丢弃它；`test_section_path_includes_each_ancestor_with_number` 期望祖先链不含自身，而 flush 在弹栈前执行）。codex 在真实夹具上量化：C2 单独使 content 从 444,976 降到 409,612（−35,364 / −7.9%）、条文数 1012→368。

Plan baseline: R1 已批准「Task 5 补齐第二遍代码 + 顺序调整」（本记录与之相容：只决定**实现路径**，不改 R1 的执行顺序与增量添加原则）。
Runtime evidence: 旧代码 `app/parser/md_parser.py:199-211`（flush 仅在 `current_content_lines` 非空时执行 → `3.0.1` 这类「标题型且无后续内容」的行从未被结算）；`:241`（`_clean_title`）；`:267`（正文型用整栈、标题型用 `title_stack[:-1]`，两者都**不含自身**）；`:187-189/:225-226/:271`（`inherit_non_clause` / `discard_section`）。逐行走查确认：Task 1 收窄后 `#### 主控项目` 已非候选行，非候选行累积进缓冲、flush 时落到最后一条真条文 `14.3.1` → **56% 归属由 Task 1 + Task 4 即已修好**。

| Commitment | Current（R1 后） | A | B | C |
|---|---|---|---|---|
| 实现结构 | 两遍重写（`_RawLine`/`_collect_candidates`/`_parse_two_pass`/`own_body`） | **单遍保留 + 预算投票** | 两遍重写 | 先调查再定 |
| Task 1 尺子 + `_extract_clause_no` 收窄 | 保留 | **保留** | 保留 | 保留 |
| Task 4 裸数字非候选 | 保留 | **保留** | 保留 | 保留 |
| R14 实现方式 | 两遍内按 `(层级,父键)` 投票 | **候选行预算投票 + 无条件 flush** | 两遍内投票 | 待定 |
| Task 3（R7 次分组单元） | 保留（增量添加） | **删除**（Task 1 后已无触发条件） | 保留 | 待定 |
| 内节点判据 | `flush()` 内用 `own_body` 过滤 | **改为「标题型且无后续内容」判内节点** | 修 `own_body` 守卫 | 待定 |
| 非条文块打标/过滤（C4） | **丢失** | **恢复** | 需补 | 待定 |
| `_clean_title`（C5） | **丢失** | **恢复**（标题型时清洗 tail） | 需补 | 待定 |
| `parent_path` 含自身（C3） | **是（错）** | **不含自身**（对齐旧行为，保 `classify_clause` 输入不变） | 需修 | 待定 |
| 既有断言预算 | 声称 2 处 | **如实列出全部受影响项** | 需重算 | 待定 |

### R2

Question D2:

D2 — 批一的核心实现走哪条路？

Project/branch/task: `main` 分支，批一实施方案的解析器核心实现。

ELI10：这份计划把解析主循环整个重写成「两遍扫描 + 兄弟投票」。外部复核逐行核对后确认，这个重写自身引入了六处缺陷，其中两处与计划自己的测试直接矛盾（一处会把正文型条文整条丢掉——在真实夹具上实测少 35,364 字符、条文从 1012 条掉到 368 条），另有一处让条文自己出现在自己的祖先链里。更关键的是：复核回到旧代码验证后发现，真正修好那 56% 正文归属的只需要两处收窄（无编号标题不再当条文、裸数字项不再当条文），而这两处收窄在**旧的单遍结构里就已经够用**——所以这个两遍重写带来的增量收益，远小于它引入的风险。真正还需要新增的只有「同层有无标题应一致」这条规则。

Stakes if we pick wrong: 选 B 要把六处缺陷逐一补掉，而且补完仍比方案 A 多一整层抽象（`_RawLine`/两遍/`own_body`），任何一处漏补都会以「条文静默消失」的形式表现出来；选 A 则要改写已批准的 R1 决议里关于第二遍代码的部分（但保留其顺序与增量添加原则），并重新核对受影响断言清单。

Recommendation: A，因为复核已在真实语料上量化出 B 的净损失（−35,364 字符 / 条文 1012→368），而 A 用更小的 diff 达成同一用户结果——符合「最小清晰改动」与「显式优于聪明」；且 56% 归属的修复经回读旧代码确认只需 Task 1 + Task 4。

Completeness: A=10/10, B=7/10, C=3/10

Pros / cons:
A) 单遍保留 + 预算投票（推荐）
  ✅ 用更小的 diff 达成同一用户结果：Task 1（新尺子 + 无编号标题非条文）+ Task 4（裸数字非候选）+ R14 预算投票 + 无条件 flush，四处即可
  ✅ 无需 `_RawLine`/`_collect_candidates`/`_parse_two_pass`/`own_body` 一层新抽象，C1/C2/C3/C6 随之消失；Task 3 也可删除
  ❌ 要改写已批准的 R1 决议中「第二遍完整代码」那一部分（顺序与增量添加原则保留）
  ❌ R14 在单遍里要做一次预算扫描，须明确它只扫候选行、不改变其它行为
B) 保留两遍重写并逐项修补
  ✅ 已批准的 R1 决议与已写好的第二遍代码大体可留用，改动集中在补漏
  ✅ 两遍结构对「先定判据、再产出」在概念上更整齐
  ❌ 要补 C1（前向依赖）、C2（丢正文型条文）、C3（祖先含自身）、C4（非条文块打标与目次过滤丢失）、C5（`_clean_title` 丢失）、C6（任务检查点不成立）共六项，任一漏补都表现为静默丢条文
  ❌ 补完后仍比 A 多一整层抽象，而这层抽象带来的增量收益经实测并不存在（56% 由 Task 1 + Task 4 即已修好）
C) 先调查再定
  ✅ 不急着改已批准的内容，可先就「56% 是否真的只需两处收窄」做一次独立复现
  ❌ 复核已给出可复现的量化证据（旧代码逐行走查 + 真实夹具），再调查的边际收益低
  ❌ 调查期间计划停留在已知有六处缺陷的状态，不可交付

Net: 真正权衡的是「保住已批准的两遍结构与已写的代码」对「用四分之一的改动量达成同一结果，并消掉六项已知缺陷」。

Header: 核心实现路径

Options:
A) 单遍保留 + 预算投票
以 Task 1（新尺子 + `_extract_clause_no` 收窄）+ Task 4（裸数字非候选）为归属修复，R14 改为「对候选行做一次预算投票 + 无条件 flush」；删除 Task 3（Task 1 后已无触发条件）；恢复非条文块打标/目次过滤（C4）与 `_clean_title`（C5）；祖先链不含自身（C3）。
B) 保留两遍重写并逐项修补
保留 `_RawLine`/`_collect_candidates`/`_parse_two_pass`/`own_body`，逐项补 C1–C6：修前向依赖、修 `own_body` 守卫、祖先链排除自身、恢复非条文块机制与 `_clean_title`、重定 Task 检查点。
C) 先调查再定
先就「56% 是否只需 Task 1 + Task 4 两处收窄」做一次独立复现，再决定路径。

State: approved
Actual answer: A) 单遍保留 + 预算投票（用户于本会话选定，2026-09-27）
Accepted scope: 保留旧的单遍解析循环结构，只做四处改动——(1) 层级改由编号点数推导（Task 1）；(2) `_extract_clause_no` 对无编号标题返回 `None`（Task 1）；(3) 裸阿拉伯数字行非候选（Task 4）；(4) R14 改为对候选行做一次预算投票 + 新候选行到达时**无条件 flush**。删除两遍重写层（`_RawLine` / `_collect_candidates` / `_parse_two_pass` / `own_body`）与 Task 3（R7 处置，Task 1 后已无触发条件）。恢复 C4（非条文块打标保留 + 目次段直接过滤）与 C5（标题型时 `_clean_title`）。修正 C3（`parent_path`/`section_path` **不含自身**，与旧行为一致以保 `classify_clause` 输入不变）。修正 C8（Task 10 的样例须让命中只来自祖先标题、不来自正文）。修正 C10（明确定义 `_match_clause_line` 的新返回形状）与 C11（如实列出受影响断言清单，不限于 2 处）。**未包含**：Task 2 的 `_is_zero_segment_node`（维持 SC-1 修正后的定义）、Task 7/8/9/11/12 的任务划分。
History: R1（已批准）决定「补齐第二遍代码 + 顺序调整」；R2 由外部复核 C1–C7 触发重开**实现路径**，保留 R1 的执行顺序与增量添加原则，但替换其实现结构。

### R3: 真实规则集下的分类漂移回归（TODOS 提案）

Finding: C8 的残留缺口 — [P2] (confidence: 8/10) Task 10 — 该 Task 的基线测试冻结的是**两条玩具规则**（`钢筋`/`模板`）下的标签分布，用于隔离 `parent_path` 的耦合。它**无法发现真实漂移**：仓库现有 110 条 `classification_rules`，重导后哪些条文的 dim 标签真的变了，玩具规则集答不出来。

Plan baseline: Task 10 只覆盖玩具规则集（C8 修正后其隔离成立——正文不含规则词，命中只来自祖先标题）。
Runtime evidence: `classification_rules` 现有 110 条（规则身份唯一键 `(维度, 关键词)`）；`parent_path` 参与 `rule_engine.py:66` 的 `augmented_text`。真实漂移需要真实规则集才能观测。

| Commitment | Current | A（建 TODO） | B（跳过） | C（本轮就做） |
|---|---|---|---|---|
| 真实规则集下的漂移回归 | 无 | 记入 TODOS.md，批一交付后执行一次 | 不做 | 并入 Task 10 一起做 |
| 数据库隔离（110 条规则） | 不需要 | 不需要（届时另搭环境） | 不需要 | 需要（复用批二 Task 15 的 `isolated_paths`） |
| 与批一交付的关系 | — | 批一交付**后**跑，不阻塞 | — | 阻塞 Task 10 完成 |

### R3

Question D3:

D3 — 要不要把「真实规则集下的分类漂移回归」记入 TODOS.md？

Project/branch/task: `main` 分支，批一工程评审的收尾。

ELI10：本批会改变送进分类引擎的祖先标题链，而分类结果直接影响条文挂哪个维度标签。计划里已经有一条测试守住这件事，但它用的是**我们自己编的两条玩具规则**——能证明「祖先标题确实进了匹配文本」，却答不出「仓库里那 110 条真实规则下，有多少条文的标签真的变了」。要看后者，得拿真实规则集把重导前后的标签分布跑一遍比一次。

Stakes if we pick wrong: 选 B 则重导后若真有大批条文标签漂移，没有任何自动机制会在你发现之前报警；选 C 则 Task 10 的完成要多依赖数据库夹具（110 条规则要建表灌入），给批一增加一处与解析器无关的依赖。

Recommendation: A，因为这条回归的价值恰在「重导真实语料之后」而不是写代码之时——批一交付时你已经会重导一次 CJJ2，那时顺手跑一次对比即可，不必现在就把数据库夹具引进纯函数任务里。

Completeness: A=9/10, B=4/10, C=8/10

Pros / cons:
A) 记入 TODOS.md（推荐）
  ✅ 时机正确：真实漂移只在重导真实语料后才有意义，而批一交付时你本来就会重导一次
  ✅ 保持 Task 10 的纯函数性质（不引入数据库依赖），与 Global Constraints 的测试隔离约定一致
  ❌ 依赖你在重导时记得跑它——TODOS 条目在本仓有被挤后的先例（T1/T2）
  ❌ 届时需要临时搭一套真实规则集与语料的对照环境
B) 跳过
  ✅ 完全不加待办，批一面最干净
  ❌ 重导后若真有大批标签漂移，本项目没有任何机制会在你察觉之前发现
  ❌ 与 CEO 评审 CRITICAL-2 的初衷相悖（那条缺陷正是「分类输入被静默改变」）
C) 本轮就做（并入 Task 10）
  ✅ 归因最完整：批一交付时同时拿到玩具隔离与真实分布两份证据
  ✅ 不必依赖你日后记得跑——写进计划就会被执行
  ❌ Task 10 从纯函数测试变成依赖数据库的测试，要多一套真实规则的建表与隔离（三处路径 patch）
  ❌ 把与解析器无关的数据库依赖引进批一，扩大本批的活动部件

Net: 权衡的是「归因完整、但要给纯函数任务引入数据库依赖」对「保持本批干净、但把发现漂移的责任交给日后」。

Header: 分类漂移回归

Options:
A) 记入 TODOS.md
把「真实规则集下、重导前后的 dim 标签分布对比」记为待办，批一交付并重导 CJJ2 后执行一次。Task 10 维持纯函数形态。
B) 跳过
不记待办。接受「重导后若有大范围标签漂移，需人工发现」。
C) 本轮就做
并入 Task 10：引入真实规则集（110 条）与数据库隔离夹具，把重导前后的标签分布对比一并做成测试。

State: approved
Actual answer: A) 记入 TODOS.md（用户于本会话选定，2026-09-27）
Accepted scope: 把「真实规则集（110 条 `classification_rules`）下、重导前后的 dim 标签分布对比」记为 **TODOS.md T19**，在批一交付并重导 CJJ2 后执行一次。**Task 10 维持纯函数形态**（不引入数据库依赖），**未包含**：把真实规则集回归并入 Task 10。
History: 由工程评审 C8 的残留缺口引出（玩具规则集无法观测真实漂移）。

## Self-Review

**1. Spec coverage**（对照 spec §4.1 的 ①–⑧ 与本批范围；**已按工程评审 R2 决议更新**）：

| spec 项 | 覆盖它的 Task | 备注 |
|---|---|---|
| ① 层级只由编号点数推导 | Task 1 | |
| ② `X.0.Y` 按 R3 处理 | Task 2 | 判据只看**末段**（SC-1 修正） |
| ③ 内节点判据「有无自身正文」 | **Task 3** | 原 Task 6，已合并 |
| ④ R14 兄弟多数表决 | **Task 3** | 原 Task 5 的两遍解析已删除（R2），改为**预扫预算投票** |
| ⑤ `section_path` 栈式构建 | **Task 3** | 原 Task 6，已合并 |
| ⑥ 目次优先（TOC-first） | **不在本批** | 已按 D6.2 降为证据触发（见下） |
| ⑦ 小修复（含 `_extract_clause_no→None`、中文检查、年份、`_clean_title`） | Task 1 / Task 3 | `_clean_title` 恢复在 Task 3（C5） |
| ⑧ 非条文块名单扩充 + 目次直接过滤 | Task 7 | C4：打标保留与过滤机制**必须原样保留** |
| §6 三项回归指标 | Task 8 | |
| **CEO 新增：守恒断言** | Task 9 | 含变异验证 |
| **CEO 新增：分类回归基线** | Task 10 | 样例已按 C8 修正，让命中只来自祖先标题 |
| **CEO 新增：面包屑覆盖率** | Task 8 | 判据已按 SC-3 修正（剔除 `0` 段） |
| **CEO 新增：重复条文号诊断** | Task 11 | |
| **R7 次分组单元内容回流** | **Task 1 + Task 4** | 评审查明：两处收窄即已修好 56% 的归属，**不需要专门的 R7 代码** |

**2. Placeholder scan**：本计划无 TBD/TODO；「待填」只出现在**必须现场实测**的基线数值处
（Task 8 Step 5 的批一验收基线表、Task 9 Step 2 的 `BASELINE_PLAIN_CHARS`），
两处都有硬门禁（`assert BASELINE_PLAIN_CHARS > 0` 与「未填不得进入下一 Task」）。

**3. Type consistency**：`_level_from_clause_no`（Task 1 定义 → Task 3 消费）、
`_is_zero_segment_node`（Task 2 定义 → Task 3 消费）、`_match_clause_line` 返回
`(clause_no, tail)` 二元组（Task 1 定形 / Task 4 收窄 → Task 3 消费）、
`_candidate_of` / `_parent_key` / `_vote_title_mode` / `_build_section_path`
（均在 Task 3 定义并按同名消费）、`_expected_ancestor_count`（Task 8 定义 → 其用例消费）。
`section_path` 键在 **Task 3** 产出、Task 8 消费（`c.get("section_path")`）。
`is_non_level_group_title` 与 `_RawLine` **已随 R2 删除**，仅在删除/合并理由与本记录中作为历史出现。

**4. 未纳入本批的项**（明确列出，避免被误认为遗漏）：
- **⑥ 目次优先**：D6.2 已决议降为**证据触发**——批一交付后读 `breadcrumb_coverage` 与
  `missing_sections`，缺口大才启用。**Task 8 的 `missing_sections` 就是那条证据。**
- **两遍重写层与 R7 专用代码**：R2 决议删除（见 Task 5/6 的存根说明）。
- `parent_path` 的保留/改名、`section_path` 格式规范入文档、`models.Clause` 同步：
  属**批二**与文档收尾。
- `_should_emit_clause` 的接口与既有 `is_non_clause` / `is_cover_clause` 语义不变。

---

# 批一工程评审产物（/plan-eng-review，2026-09-27）

**Target**：本文件（`docs/superpowers/plans/2026-09-27-batch1-parser-hierarchy.md`）
**Reviewer**：native（当前 harness，模型身份未报告）+ 外部复核 **codex**（`outside_status: completed`）
**Mode**：SCOPE_REDUCED（Scope Challenge 结论：范围按建议缩减——R2 删除两遍重写层与 R7 专用代码，核心改动量降到约四分之一）

## Approval readiness

**PASS**。逐条核对（`## Decision ledger` 内）：

| 项 | 状态 | 依据 |
|---|---|---|
| SC-1（P0）`_is_zero_segment_node` 判据收窄为末段 | 已应用 | 事实性更正（计划与 182 号及自身测试矛盾），无需另批 |
| SC-2（P1）三处 `_NUM_PATTERNS` 解包点 | 已应用 | 同上（不改会 `ValueError`） |
| SC-3（P1）覆盖率/残余缺口判据剔除 `0` 段 | 已应用 | 同上（判据对 R3 系统性误判） |
| SC-4（P1）变异验证改为可执行 | 已应用 | 同上（原指令不可执行） |
| A1（P2）次分组单元标题带 `####` 进正文 | 已应用 | 同上（标记混入正文） |
| A2（P3）祖先栈含正文句作面包屑标签 | **已消解** | R2 新设计里正文型行 `title` 为空，`_build_section_path` 渲染为纯编号（如 `"3.0.1"`），不存在正文句污染 |
| Q1（P1）`_build_section_path` 前向依赖 | 已应用 | R2：与调用方同处 Task 3 |
| Q2（P2）Task 3 引用旧单遍变量 | 已应用 | R2：改为在 `_candidate_of` 上表达 |
| SC-5 / SC-6 | 已裁定 | R1（顺序与增量添加原则）+ R2（实现结构）；SC-6 平票规则落在 Task 3 |
| **R1** | **approved 且已应用** | 用户裁定 A（本会话） |
| **R2** | **approved 且已应用** | 用户裁定 A（本会话） |
| **R3**（TODO 提案） | **approved 且已应用** | 用户裁定 A（本会话）→ TODOS.md **T19** |
| C1–C6、C10、C11（外部复核） | 已应用 | 均为事实性更正或随 R2 消解 |
| C8 | 已应用 | 样例改为只由祖先标题命中 + 补反向断言 |
| C9 | 记录 | `level` 无生产消费者；`parent_path` 是唯一生产耦合，已由 C3 修正与两条测试守住 |

无未批准的补救项，无被推迟的必需补救项。

## "NOT in scope"

| 项 | 推迟/否决理由 |
|---|---|
| 两遍重写层与 R7 专用代码 | **R2 否决**（外部复核复现 6 项缺陷；56% 归属由两处收窄即已修好） |
| 目次对齐（TOC-first） | **D6.2 降为证据触发**：批一交付后读 `breadcrumb_coverage` 与 `missing_sections` 再定 |
| 真实规则集下的分类漂移回归 | **R3 → TODOS T19**（批一交付并重导后执行一次） |
| `parent_path` 改名、`section_path` 格式规范入文档、`models.Clause` 同步 | **批二**与文档收尾 |
| 重导期间检索可用性不变量化（写锁/禁用） | CEO 评审未决项，本批未使其变差 |
| `_vector_ids_and_state` 只读 `clause_id`（性能） | **批二**（该函数在批一不被触及） |

## "What already exists"（复用而非重建）

| 既有资产 | 处置 |
|---|---|
| `parse_markdown` 的**单遍循环** | **保留**（R2 只做四处改动，不重建）——两遍重写的增量收益经实测不存在 |
| `_looks_like_title` | **保留**，角色从「终判」变为「投票输入」（R14 的兄弟一致性由组的多数决定） |
| `_should_emit_clause` / `is_non_clause_title` / `is_filter_non_clause_title` / `is_cover_clause` | 语义不变，接口不变 |
| `_clean_title` | Task 3 恢复使用（标题型行清洗 tail，工程评审 C5） |
| `tests/test_md_parser.py` 既有 34 个用例 | 作为回归覆盖；其中 5 处按设计变更（见 Global Constraints 表），3 条过松断言**须收紧** |
| `tests/conftest.py` 的 `monkeypatch` 隔离惯例 | Task 10 沿用（历史 learning 9/10） |

**未采纳的共享代码提取**：本批**无**新增重复——`_candidate_of` 是**取代**旧的解析循环判定，不是复制；
`_parent_key` 被预扫与正式解析两处共用（这是必要条件：两者判据不一致则投票对不上行）。
无提取机会，故不进入共享代码评分的「至少两个已验证调用点」流程。

## Diagrams

**1. 单遍数据流（改造前 → 改造后）**

```
改造前（三处缺陷）
  行 ──▶ _match_clause_line/_looks_like_title ──▶ 行内判定标题/正文
          │
          └─ 裸编号项/无编号标题 → 当条文或丢内容 → 主控项目 吞 252,514 字符
          └─ 标题型且无后续内容 → **从未 flush** → 3.0.1 消失
          └─ flush 用整栈 → parent_path 语义依赖 push/pop 时机

改造后（四处改动）
  行 ──▶ _candidate_of ──▶ 候选行 ──┬─▶ _vote_title_mode（预扫，(层级,父键) 分组多数）
                                      │
                                      └─▶ 主循环 ──▶ 新候选行到达 → flush（**无条件**）
                                                    │
                                                    ├─ has_own_body = title=="" or content!=""
                                                    │    └─ 假 → 内节点（只作祖先，不入库）
                                                    ├─ ancestors = stack[:-1]  ← 不含自身
                                                    └─ section_path = "no title > no title"
  非候选行 ──▶ pending（剥掉 `####`）──▶ 落到**当前条**
```

**2. R14 预扫的投票分组**

```
  3.0.1 接头设计应满足强度及变形性能的要求     ← _looks_like_title=True  ┐
  3.0.2 接头安装应符合本规程第2章的规定。      ← False                  ├ 组 (3, '3') 多数=False
  ...                                                                  │  → 全部判「无标题」
  3.0.9 接头安装应符合本规程第9章的规定。      ← False                  ┘  → 3.0.1 正文归自身 ✓
```

**3. 错误流**

```
  _candidate_of 与 _vote_title_mode 判据不一致 ──▶ 投票对不上行
      └─ 设计上消除：两者**共用** _candidate_of 与 _parent_key（各仅一处定义）

  内节点误判（有正文者被判无正文）──▶ 条文整条不产出（静默）
      └─ 兜底：Task 9 守恒断言（正文总量不减少）+ 变异验证证明其可失败

  目次段误判 ──▶ 段内内容全丢（静默）
      └─ 兜底：test_toc_section_is_discarded_entirely

  parent_path 含自身复发 ──▶ classify_clause 输入漂移（静默）
      └─ 兜底：test_parent_path_excludes_self + Task 10 的双向断言
```

## Failure modes

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| `_vote_title_mode` 分组错 | title/content 互换 | N | Y | 检索仍能命中（两者都进索引） | N |
| 内节点误判 | 条文整条不产出 | N | Y（守恒断言 + 变异验证） | Silent 但被守恒门禁拦下 | N |
| `_candidate_of` 收窄过度 | 合法条文不入库 | N | Y（附录A 边界 + 守恒断言） | Silent 但被守恒门禁拦下 | N |
| `_candidate_of` 收窄不足 | 伪条文号回流 | N | Y（`fake_clause_no_count` ≤ 目标） | 结果集噪声 | N |
| 目次段误判 | 目录行泄漏进正文 | N | Y（`test_toc_section_is_discarded_entirely`） | 正文含目录行 | N |
| `parent_path` 含自身 | 分类标签漂移 | N | Y（`test_parent_path_excludes_self` + Task 10） | 标签改变 | N |
| 层级尺子切换 | 深层级错位 | N | Y（2 处层级断言 + 覆盖率指标） | section_path 分段异常 | N |

**CRITICAL GAPS：0 条。** 依据：上表每一条静默路径都已有**命名测试**，且其中「条文丢失」这一类
统一由 Task 9 的**守恒断言**兜住——该断言本身经**变异验证**（Task 9 Step 3 用 SC-1 的真实缺陷
作为变异体，要求断言必须失败）。按规则「无测试 + 无错误处理 + 静默」才算关键缺口，本批无此类行。

## Worktree parallelization strategy

**Sequential implementation, no parallelization opportunity.**

依据：唯一的生产文件是 `app/parser/md_parser.py`；Task 1 → Task 2 → Task 4 → Task 3 是**同一条
函数调用链上的顺序改动**（尺子 → 判据 → 收窄 → 主循环），Task 7–12 中的 Task 8/9 消费 Task 3 的产出，
Task 12 是整体验收。无第二处独立工作流可并行。

## Implementation Tasks

Synthesized from this review's findings. 每条都源自上面的具体发现；未新增无来源的任务。

- [ ] **T1 (P0, human: ~1h / CC: ~5min)** — `app/parser/md_parser.py` — `_is_zero_segment_node` 判据收窄为**末段为 0**
  - Surfaced by: Scope Challenge SC-1 — 初稿「任意段为 0」会让所有 `X.0.Y` 条文不入库（实测 JGJ107 39 条 / 44,353 字符 = 80%、CJJ2 44 条 / 12,796 字符）
  - Files: `app/parser/md_parser.py`、`tests/test_md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -k zero_segment -v`；且守恒断言在改造前后差值为正
- [ ] **T2 (P1, human: ~30min / CC: ~3min)** — `app/parser/md_parser.py` — 三处 `_NUM_PATTERNS` 解包点全部改单值
  - Surfaced by: Scope Challenge SC-2 — `:113`（`_match_clause_line`）与 `:299`（`_extract_title`）漏改会 `ValueError`
  - Files: `app/parser/md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -k all_num_patterns_loops -v`（源码断言须计到 3 处）
- [ ] **T3 (P1, human: ~1h / CC: ~5min)** — `scripts/survey_structure.py` — 覆盖率与残余缺口判据剔除 `0` 段
  - Surfaced by: Scope Challenge SC-3 — 原判据 `len(segs) == level-1` 把 R3 的 `X.0.Y` 系统性误判为不完整（占 JGJ107 53%），会错误地让目次对齐显得有必要（D6.2 用该指标决策）
  - Files: `scripts/survey_structure.py`、`tests/test_md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -k expected_ancestor_count -v`
- [ ] **T4 (P1, human: ~30min / CC: ~3min)** — `docs/.../batch1...md` Task 9 — 变异验证改为一行可复现变异
  - Surfaced by: Scope Challenge SC-4 — 原指令引用已被 Task 4 改掉的代码形态且语义自相矛盾
  - Files: 本计划文件（Task 9 Step 3）
  - Verify: 施加变异后 `tests/test_parse_conservation.py` **必须失败**，还原后通过
- [ ] **T5 (P1, human: ~4h / CC: ~20min)** — `app/parser/md_parser.py` — 核心改单遍：R14 预算投票 + 无条件 flush + 内节点判据 + `section_path`
  - Surfaced by: 外部复核 C1–C7 → Decision ledger **R2**
  - Files: `app/parser/md_parser.py`、`tests/test_md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -v`（含 `test_r14_sibling_majority_rescues_long_untitled_clause`、`test_body_type_clause_emitted_without_following_lines`、`test_group_heading_content_flows_to_enclosing_clause`）
- [ ] **T6 (P1, human: ~30min / CC: ~3min)** — `app/parser/md_parser.py` — `parent_path` / `section_path` 取 `stack[:-1]`，**不含自身**
  - Surfaced by: 外部复核 C3 — 两遍设计在弹栈前 flush，致条文出现在自己的祖先链里；`parent_path` 是 `classify_clause` 的输入
  - Files: `app/parser/md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -k excludes_self -v`
- [ ] **T7 (P1, human: ~2h / CC: ~10min)** — `app/parser/md_parser.py` — 恢复非条文块打标保留 + 目次段直接过滤
  - Surfaced by: 外部复核 C4 — 重写若丢掉 `inherit_non_clause` / `discard_section`，`前言`/`条文说明` 不再入库、目录行泄漏进正文
  - Files: `app/parser/md_parser.py`、`tests/test_md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -k "non_clause_blocks or toc_section or qianyan or tiaowenshuoming" -v`
- [ ] **T8 (P2, human: ~30min / CC: ~3min)** — `app/parser/md_parser.py` — 标题型行恢复 `_clean_title(tail)`
  - Surfaced by: 外部复核 C5 — 旧实现 `:241` 会清洗，新代码丢弃后 `test_multi_space_title_cleanup` 失败
  - Files: `app/parser/md_parser.py`
  - Verify: `pytest tests/test_md_parser.py -k multi_space -v`
- [ ] **T9 (P2, human: ~1h / CC: ~5min)** — `tests/test_classify_baseline.py` — 样例改为只由祖先标题命中 + 补反向断言
  - Surfaced by: 外部复核 C8 — 初稿样例正文本身就含规则词，今天就会通过，冻结的是与本次改动无关的玩具分布
  - Files: `tests/test_classify_baseline.py`
  - Verify: `pytest tests/test_classify_baseline.py -v`；`test_label_disappears_when_ancestor_chain_is_broken` 必须通过
- [ ] **T10 (P2, human: ~1h / CC: ~5min)** — `tests/test_md_parser.py` — 收紧 3 条过松断言（`parent_path` 那几条）
  - Surfaced by: 外部复核 C11 — `test_parse_markdown_parent_inheritance` 与 `test_parse_appendix_clauses` 只因 `len>=2` / `any(...)` 过松才没拦住 `parent_path` 漂移
  - Files: `tests/test_md_parser.py`
  - Verify: 收紧后改造前的实现**必须失败**（能拦住 C3 那类漂移）
- [ ] **T11 (P2, human: ~15min / CC: ~2min)** — `app/parser/md_parser.py` — 明确定义 `_match_clause_line` 返回 `(clause_no, tail)`
  - Surfaced by: 外部复核 C10 — Task 1 只写「删掉算 level_base 的那行」，未给出新返回形状
  - Files: `app/parser/md_parser.py`
  - Verify: pyright 0 error（返回形状变更后无解包错误）
- [ ] **T12 (P2, human: ~15min / CC: ~2min)** — `app/parser/md_parser.py` — 非候选行的 `####` 井号剥除
  - Surfaced by: Architecture review A1 — 次分组单元标题行带 `####` 进正文，`plain_text` 不去 Markdown 标记
  - Files: `app/parser/md_parser.py`
  - Verify: `test_group_heading_content_flows_to_enclosing_clause` 断言 `"####" not in body`
- [ ] **T13 (P3, human: ~2h / CC: ~10min)** — 一次性对照环境 — 真实规则集下的分类漂移回归
  - Surfaced by: 外部复核 C8 残留缺口 → Decision ledger **R3** → **已记入 TODOS.md T19**
  - Files: 待定（届时新建）
  - Verify: 输出改造前后各 dim 的标签分布 diff

**批一原本就有的任务**（来源 = CEO 评审，已在本计划的 Task 1–12 中，不在本清单重复）：
守恒断言（Task 9）、分类玩具基线（Task 10）、面包屑覆盖率（Task 8）、重复条文号诊断（Task 11）、
结构勘察脚本与固定夹具（Task 8）、批一验收（Task 12）。

## Unresolved decisions

**本评审无未决项。** R1、R2、R3 均已取得裁定并落地；SC-1~SC-6、A1~A2、Q1~Q2、C1~C11 全部已应用或已消解。

## Completion summary

- **Step 0: Scope Challenge** — 范围按建议调整（R2 把核心改动量缩减到约四分之一：删除两遍重写层与 R7 专用代码）
- **Architecture Review** — 2 issues found（A1 已修、A2 随 R2 消解）
- **Code Quality Review** — 2 issues found（Q1、Q2 均随 R2 消解）
- **Test Review** — 覆盖图已产出；识别 3 处缺口（三处解包守卫、`_expected_ancestor_count`、正文型无后续行仍产出）并已补；另补 `test_toc_section_is_discarded_entirely`
- **Performance Review** — 0 issues found（单遍 O(n)；`_candidate_of` 在预扫与主循环各扫一遍，总量仍为 O(n)）
- **NOT in scope** — 已写（6 项）
- **What already exists** — 已写（6 项复用 + 无共享代码提取机会）
- **TODOS.md updates** — 1 项提议并经裁定采纳（**T19**）
- **Failure modes** — 7 条，**0 条关键缺口**
- **Unresolved decisions** — **0**（本评审）
- **Outside voice** — provider=**codex**，`outside_status: completed`（11 项发现，全部经回查确认）
- **Parallelization** — 0 lanes，**顺序实施**（无并行机会）
- **Lake Score** — 3/3：三项有 10/10 选项的覆盖率问题（R2、R3、SC-5 的 R1）中，用户选了 2 项 10/10（R2、R3）与 1 项被 R2 取代的选项；按「已答且提供 10/10 选项」口径计 **2/2**

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | ISSUES OPEN | mode: HOLD_SCOPE, 8 critical gaps |
| Outside Review | `codex exec`（plan review，本批） | Independent 2nd opinion | 3 | completed | 11 findings; 9 resolved in plan; 1 → R2; 1 → TODOS T19 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | ISSUES OPEN | 13 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** provider=codex, phase=plan-review, status=completed, findings=11（本批）；累计 3 次 codex 复核（本会话第 1 次为 CEO 评审的 9 项、第 2 次为本批的 11 项、第 3 条为历史记录）。codex 逐行核对计划与源码，并在真实 CJJ2 夹具上复现了缺陷（−35,364 字符 / 条文 1012→368），其「退回重写核心」的建议经 R2 采纳。
- **CROSS-MODEL:** 两处独立一致：① **`parent_path` 是分类引擎的输入**（我在 CEO 评审发现、codex 在本批复核独立确认，并指出会因祖先链含自身而静默漂移）；② **两遍重写的增量收益不存在**（我测得旧代码走查结论、codex 在真实夹具复现量化）。一处分歧：codex 建议「退回重写」时倾向把 Task 3 一并删除；我在 R2 中保留了 Task 3 的产物（`section_path` 与内节点判据），仅删除其两遍结构与 R7 专用代码。模型身份：native 为当前 harness（未报告具体模型），external 为 codex（`gpt-6-astra`）。
- **VERDICT:** 本批 **ENG 未 CLEAR**（`issues_open`：13 项发现虽已全部应用，但计划尚未实施，无验证证据）。**CEO 评审亦为 ISSUES OPEN**（8 条 CRITICAL GAP 属批一/批二实施层）。**eng review required** —— 建议在批一代码落地并重导 CJJ2 后，对本计划重跑一次 `/plan-eng-review` 以**验证**（而非再设计）已应用项。

NO UNRESOLVED DECISIONS
