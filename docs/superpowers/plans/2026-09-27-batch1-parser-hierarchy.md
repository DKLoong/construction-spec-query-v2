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
- **不得放宽既有断言**：唯一允许修改的既有断言是「层级尺子按设计变更」那几处（Task 1 Step 1 逐一列出），且必须在提交信息中说明理由。

---

## File Structure

| 文件 | 责任 | 本批动作 |
|---|---|---|
| `app/parser/md_parser.py` | 唯一的解析实现；本批的**全部**生产改动集中于此 | 重构 |
| `tests/test_md_parser.py` | 解析器单元测试（既有 452 行） | 修改既有断言 + 新增用例 |
| `scripts/survey_structure.py` | **新建**：结构勘察 + 三项回归指标 + 面包屑覆盖率（Task 11） | 新建 |
| `tests/test_parse_conservation.py` | **新建**：守恒断言（Task 9） | 新建 |
| `tests/test_classify_baseline.py` | **新建**：分类标签分布回归（Task 10） | 新建 |
| `tests/fixtures/cjj2_source.md` | **新建**：CJJ2 源 md 的固定副本，供守恒/覆盖率测试使用（不依赖 `data/outputs/`） | 新建 |

> **为何固定夹具副本**：`data/` 是运行时目录，测试不得依赖它（且历史上 `data/uploads/` 已被 pytest 污染）。夹具取 `data/outputs/f543577f/f543577f.md` 的当前内容，一次性拷入 `tests/fixtures/`。

---

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

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: PASS

- [ ] **Step 5: 补三个小修复的针对性用例**

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

**Files:**
- Modify: `app/parser/md_parser.py`（新增判定函数，Task 5 的第二遍会调用）
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: `_level_from_clause_no`（Task 1）
- Produces: `_has_zero_segment(clause_no: str) -> bool`

- [ ] **Step 1: 写失败测试**

```python
def test_zero_segment_is_not_a_node():
    """R3：章内不分节时条编号的节位用 0 表示（3.0.1）。该 0 段不构成节点。

    even 若文档里真的写了 `### 3.0 某某节`，也不得成为 3.0.1 的父级。
    """
    md = ("### 3 章名\n\n### 3.0 不应存在的节\n\n"
          "3.0.1 接头设计应满足强度要求。\n")
    results = parse_markdown(md)
    r = [c for c in results if c["clause_no"] == "3.0.1"]
    assert len(r) == 1
    assert r[0]["parent_path"] == ["章名"]        # 父链里没有 `3.0` 那一级
    assert all(c["clause_no"] != "3.0" for c in results)  # 3.0 本身不入库

def test_ten_is_not_a_zero_segment():
    """`10.1` 的 10 不是 0 段"""
    assert _has_zero_segment("10.1") is False
    assert _has_zero_segment("3.0.1") is True
    assert _has_zero_segment("1.0") is True
    assert _has_zero_segment("3") is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py::test_zero_segment_is_not_a_node -v`
Expected: FAIL — `ImportError: cannot import name '_has_zero_segment'`

- [ ] **Step 3: 实现**

```python
def _has_zero_segment(clause_no: str) -> bool:
    """R3（182 号第三十五条）：章内不分节时，条编号中对应节的编号用 "0" 表示。

    `3.0.1` 的 `0` 段表示「本章不分节」，因此不构成一个层级节点：
    它既不入库、也不进入后代条文的父链（父链 = ['3 章名']）。

    注意按**段**判断，避免误伤 `10.1`（首段 10 不是 0）。
    """
    parts = clause_no.split('.')
    return len(parts) > 1 and '0' in parts
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "zero_segment or ten_is_not" -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "feat: R3 的 0 段不构成层级节点

按 182 号第三十五条，章内不分节时条编号的节位用 0 表示（3.0.1），
该段不是层级节点：不入库、也不进入后代父链。按段判断，10.1 不受影响。"
```

---

## Task 3: 次分组单元（R7）不产生层级，其内容回流到所属条

> **本 Task 是批一的核心**：实测 124 条伪条文号吞掉 252,514 字符（占全部 content 的 56%），
> 其中 `一般项目` 单条最多吞 9,795 字符。来源就是这个：`#### 一般项目`（次分组单元）被当成标题行，
> 其下的裸编号项与 `检查数量：`/`检验方法：`/表格 全部挂到了它身上，而不是所属的条 `14.3.1`。

**Files:**
- Modify: `app/parser/md_parser.py`
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: `_level_from_clause_no`、`_extract_clause_no`（Task 1）
- Produces: `is_non_level_group_title(title: str) -> bool`

- [ ] **Step 1: 写失败测试（用真实的 CJJ2 结构）**

```python
def test_group_heading_does_not_become_node():
    """R7：主控项目/一般项目是次分组单元，不是层级，也不入库。

    它们的「内容」（裸编号项 + 检查数量 + 检验方法 + 表格）必须回流到所属的条。
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

检验方法：观察，用卡尺检查。

14.3.2 钢梁现场安装检验应符合下列规定：

正文乙。
"""
    results = parse_markdown(md)
    nos = [c["clause_no"] for c in results]
    # 次分组单元与裸编号项都不成条文
    assert "主控项目" not in nos
    assert "一般项目" not in nos
    assert "1" not in nos and "6" not in nos
    # 它们的内容全部回流到所属的条 14.3.1
    c = [r for r in results if r["clause_no"] == "14.3.1"]
    assert len(c) == 1
    body = c[0]["content"]
    assert "钢材的品种" in body          # 主控项目下的裸编号项
    assert "焊缝外观质量" in body        # 一般项目下的裸编号项
    assert "检查数量：全数检查。" in body  # 裸编号项后的检查数量
    assert "检验方法：观察，用卡尺检查。" in body  # 与表格
    # 次分组单元的名字仍应保留在内容里（信息不丢，只是不再是节点）
    assert "主控项目" in body and "一般项目" in body
    # 下一条不受污染
    assert [r for r in results if r["clause_no"] == "14.3.2"][0]["content"].strip() == "正文乙。"

def test_group_title_with_roman_prefix_matches():
    """带罗马数字前缀的次分组单元（Ⅰ/Ⅱ/Ⅲ…与 Ⅰ/Ⅱ 半角变体）同样识别"""
    for t in ("主控项目", "一般项目", "Ⅰ 主控项目", "Ⅱ 一般项目", "Ⅲ 主控项目"):
        assert is_non_level_group_title(t) is True
    for t in ("一般规定", "模板安装", "主控项目质量要求"):
        assert is_non_level_group_title(t) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "group_heading or roman_prefix" -v`
Expected: FAIL — `ImportError: cannot import name 'is_non_level_group_title'`

- [ ] **Step 3: 实现名单与判定**

```python
# R7（182 号第三十三条）：次分组单元——节内容较多时插入的分组，
# 编号用大写罗马数字，**不构成层级**；所属节的条文编号仍连续。
# 实测 CJJ2 的 `#### 主控项目` / `#### 一般项目` 即此类（OCR 常丢掉罗马数字前缀）。
_NON_LEVEL_GROUP_TITLES = {"主控项目", "一般项目"}

# 罗马数字前缀（大写罗马数字 U+2160–U+216F 与 ASCII I/V/X 变体）
_ROMAN_PREFIX = re.compile(r'^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|[IVXivx]+)[\s.、]*')


def _strip_roman_prefix(title: str) -> str:
    """去掉次分组单元的罗马数字编号前缀，便于按名称判定。"""
    return _ROMAN_PREFIX.sub('', (title or '').strip()).strip()


def is_non_level_group_title(title: str) -> bool:
    """判断标题是否为次分组单元（R7）：是分组标签，不是层级节点。

    识别按「去掉罗马数字前缀后的完整名称」精确匹配，不用子串——
    避免误伤「主控项目质量要求」这类真实标题。
    """
    t = (title or "").strip()
    if not t:
        return False
    if t in _NON_LEVEL_GROUP_TITLES:
        return True
    return _strip_roman_prefix(t) in _NON_LEVEL_GROUP_TITLES
```

在 `parse_markdown` 的 `#` 路径与编号行路径中，判定为次分组单元时：**不推入 `title_stack`**，但把其标题文本作为一行内容追加到当前内容缓冲（这样 `主控项目` 字样不丢，且它自然落到所属条的 content 里）：

```python
                if is_non_level_group_title(title):
                    # R7 次分组单元：不入栈（不是层级），其标签文本与后续内容
                    # 一并归入当前打开的那条（本函数第二遍中的 stack[-1]）
                    current_content_lines.append(title)
                    continue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "group" -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "feat: R7 次分组单元不产生层级，其内容回流到所属条

实测 124 条伪条文号吞掉 252,514 字符（占 content 总字符 56%），根因是
主控项目/一般项目（R7 次分组单元）被当标题行，其下裸编号项与检查数量/
检验方法/表格全挂到它身上。改为不推入 title_stack，标签文本与后续内容
一并归入所属的条。"
```

---

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

## Task 5: 两遍解析 + R14 兄弟多数表决

> 本 Task 重构 `parse_markdown` 的主体。**必须在 Task 1–4 之后**：前四个 Task 已经把
> 「什么是候选行」收窄完毕，表决才不会被裸编号项与次分组单元污染。

**Files:**
- Modify: `app/parser/md_parser.py:152-292`（`parse_markdown` 主体）
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: `_level_from_clause_no`、`_extract_clause_no`、`_has_zero_segment`、`is_non_level_group_title`
- Produces:
  - `_RawLine`（dataclass）：`index:int, clause_no:str, tail:str, level:int, parent_key:str, own_body:bool`
  - `_vote_title_mode(rows: list[_RawLine]) -> dict[tuple[int,str], bool]` — 键 `(level, parent_key)`，值 `True` = 该组为「带标题条」
  - `parse_markdown(md_text: str) -> list[dict]` — 签名与返回结构不变

- [ ] **Step 1: 写失败测试（R14 + 退化 + 不得放宽既有行为）**

```python
def test_r14_sibling_majority_rescues_long_untitled_clause():
    """R14 / GB/T 1.1 §7.3.3：同层各条有无标题应一致。

    `3.0.1 接头设计应满足强度及变形性能的要求`（16 字、无句号）旧实现按
    ≤20 字判为标题型 → 正文丢给同行标题；同层 3.0.2~3.0.9 皆为无标题条，
    多数表决应判它也无标题 → 正文归其自身 → 不丢。
    """
    md = "\n\n".join(
        ["3.0.1 接头设计应满足强度及变形性能的要求"] +
        [f"3.0.{i} 接头安装应符合本规程第{i}章的规定。" for i in range(2, 10)]
    )
    results = parse_markdown(md)
    r = [c for c in results if c["clause_no"] == "3.0.1"]
    assert len(r) == 1
    assert r[0]["title"] == ""                                  # 判为无标题条
    assert "接头设计应满足强度及变形性能的要求" in r[0]["content"]  # 正文不丢

def test_r14_group_of_titles_stays_titles():
    """同层多为带标题条时，短标题仍判为标题"""
    md = "3.0.1 一般规定\n\n正文甲。\n\n3.0.2 材料要求\n\n正文乙。\n\n3.0.3 检验方法\n\n正文丙。\n"
    titles = {c["clause_no"]: c["title"] for c in parse_markdown(md)}
    assert titles["3.0.1"] == "一般规定"
    assert titles["3.0.3"] == "检验方法"

def test_degenerate_single_item_group_falls_back_to_own_verdict():
    """退化：某组只有一条（无兄弟可表决）→ 退回该行自身的判据，行为确定"""
    md = "7.0.1 单独一条很短\n\n正文。\n"
    r = [c for c in parse_markdown(md) if c["clause_no"] == "7.0.1"]
    assert len(r) == 1
    assert r[0]["title"] == "单独一条很短"

def test_body_type_clause_keeps_tail_in_content():
    """既有行为不得回归：正文型编号行的编号后文本进 content、title 为空"""
    md = "1.0.1  为在混凝土结构中使用钢筋机械连接，制定本规程。\n"
    r = parse_markdown(md)
    assert r[0]["title"] == ""
    assert r[0]["content"] == "为在混凝土结构中使用钢筋机械连接，制定本规程。"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "r14 or degenerate" -v`
Expected: FAIL — `test_r14_sibling_majority_rescues_long_untitled_clause` 中 `title` 为 `接头设计应满足强度及变形性能的要求` 且 `content` 为空

- [ ] **Step 3: 实现两遍解析**

```python
from dataclasses import dataclass


@dataclass
class _RawLine:
    """第一遍收集的候选行。**只记录事实，不含标题/正文判断**。"""
    index: int          # 原文行号
    clause_no: str      # 编号（已归一化全角点）
    tail: str           # 编号后的文本
    level: int          # 1 + 编号点数
    parent_key: str     # 父节点键（父的 clause_no；无父为 ''）
    own_body: bool      # 其后、下一个候选行之前是否存在非空内容行


def _collect_candidates(lines: list[str]) -> list[_RawLine]:
    """第一遍：收集候选行。父键只用「编号 + 层级」推导，与标题/正文判定无关，
    因此可在第一遍算准（这也是兄弟分组可行的前提）。"""
    rows: list[_RawLine] = []
    ancestors: list[tuple[int, str]] = []   # [(level, clause_no)]

    # 先建立「行号 → 候选」映射，便于 own_body 的前瞻判断
    for i, line in enumerate(lines):
        m_hash = re.match(r"^(#{1,6})\s+(.+)$", line)
        clause_no = None
        tail = ""
        if m_hash:
            raw_title = m_hash.group(2).strip()
            clause_no = _extract_clause_no(raw_title)
            if clause_no is None or _BARE_YEAR.match(clause_no):
                continue
            if not re.search(r'[一-鿿]', raw_title):
                continue
            title = _clean_title(_extract_title(raw_title))
            if is_non_level_group_title(title):
                continue        # R7 次分组单元：不是候选行
            tail = title
        else:
            m_num = _match_clause_line(line)
            if not m_num:
                continue
            _, clause_no, tail = m_num

        level = _level_from_clause_no(clause_no)
        if _has_zero_segment(clause_no):
            # R3 的 0 段不构成节点：不作为祖先、也不成条文
            continue
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        parent_key = ancestors[-1][1] if ancestors else ""
        rows.append(_RawLine(i, clause_no, tail, level, parent_key, own_body=False))
        ancestors.append((level, clause_no))

    # 回填 own_body：本候选行与其后第一个候选行之间是否有非空且非候选的内容行
    next_index = {rows[k].index: (rows[k + 1].index if k + 1 < len(rows) else len(lines))
                  for k in range(len(rows))}
    for r in rows:
        for j in range(r.index + 1, next_index[r.index]):
            if lines[j].strip():
                r.own_body = True
                break
    return rows


def _vote_title_mode(rows: list[_RawLine]) -> dict[tuple[int, str], bool]:
    """R14 兄弟多数表决：按 (层级, 父键) 分组，多数决定该组是「带标题条」还是
    「无标题条」。组内只有一条（无兄弟）时退回该行自身的判据，行为确定。"""
    groups: dict[tuple[int, str], list[_RawLine]] = {}
    for r in rows:
        groups.setdefault((r.level, r.parent_key), []).append(r)
    verdict: dict[tuple[int, str], bool] = {}
    for key, members in groups.items():
        yes = sum(1 for m in members if _looks_like_title(m.tail))
        if yes * 2 == len(members):            # 平票（含单条：1 vs 1 不可能，见下）
            verdict[key] = _looks_like_title(members[0].tail)
        else:
            verdict[key] = yes * 2 > len(members)
    return verdict
```

> **平票语义**：`yes * 2 == len(members)` 只在偶数条且恰好半数时为真；`len(members) == 1` 时 `yes*2` ∈ {0, 2}，都不等于 1，故单条组由多数规则直接决定（`yes=1` → 判为带标题）。**这条必须在 Step 4 的测试里被锁定**（`test_degenerate_single_item_group_falls_back_to_own_verdict`）。

第二遍生成条文（同一次改写中完成）：用 `verdict` 决定每条候选行是标题型还是正文型；`own_body` 为假者按内节点处理（只做祖先、不入库）。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: PASS（含 Task 1–4 的全部用例）

- [ ] **Step 5: pyright + 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "feat: 两遍解析 + R14 兄弟多数表决

第一遍只收集候选行（编号/层级/父键/有无自身正文），不做标题判断；
按 (层级, 父键) 分组做兄弟多数表决，第二遍按表决结果生成条文。
修掉 3.0.1 这类 16 字无句号的长正文被误判为标题后丢失正文的问题。
单条组由多数规则直接决定，行为确定并有测试锁定。"
```

---

## Task 6: 内节点判据「有无自身正文」+ `section_path` 栈式构建

**Files:**
- Modify: `app/parser/md_parser.py`
- Test: `tests/test_md_parser.py`

**Interfaces:**
- Consumes: `_RawLine`、`_vote_title_mode`（Task 5）
- Produces: 每个 clause dict 新增键 `section_path: str`（面包屑，如 `"6 混凝土分项工程 > 6.1 模板"`）

- [ ] **Step 1: 写失败测试**

```python
def test_inner_node_without_body_is_not_emitted_but_serves_as_ancestor():
    """内节点判据 = 有无自身正文（不是「是章/节还是条」）"""
    md = ("## 6 混凝土分项工程\n\n### 6.1 模板\n\n"
          "#### 6.1.1 一般规定\n\n模板及其支架应进行设计。\n")
    results = parse_markdown(md)
    nos = [c["clause_no"] for c in results]
    assert "6" not in nos and "6.1" not in nos       # 无自身正文 → 内节点，不入库
    assert "6.1.1" in nos

def test_appendix_with_own_body_is_a_leaf_clause():
    """附录A 有自身正文（CJJ2 是 35K 字符验收记录表）→ 叶条文，必须入库可检索（R11）"""
    md = "附录A 验收记录表\n\n<table><tr><td>序号</td><td>项目</td></tr></table>\n"
    results = parse_markdown(md)
    r = [c for c in results if c["clause_no"] == "附录A"]
    assert len(r) == 1
    assert "序号" in r[0]["content"]

def test_section_path_includes_each_ancestor_with_number():
    md = "## 6 混凝土分项工程\n\n### 6.1 模板\n\n#### 6.1.1 一般规定\n\n正文甲。\n"
    r = [c for c in parse_markdown(md) if c["clause_no"] == "6.1.1"][0]
    assert r["section_path"] == "6 混凝土分项工程 > 6.1 模板"

def test_section_path_has_no_trailing_separator_when_root():
    """无祖先时 section_path 为空串（不是 '>' 或带分隔符的残串）"""
    r = parse_markdown("1.0.1 正文甲。\n")[0]
    assert r["section_path"] == ""

def test_no_cross_chapter_leak():
    """7.0.1 的 section_path 不得混进第 6 章的标题"""
    md = ("## 6 混凝土分项工程\n\n### 6.1 模板\n\n"
          "#### 6.1.1 一般规定\n\n正文甲。\n\n"
          "## 7 预应力分项工程\n\n7.0.1 预应力筋应抽样检验。\n")
    r = [c for c in parse_markdown(md) if c["clause_no"] == "7.0.1"][0]
    assert "混凝土" not in r["section_path"]
    assert "预应力" in r["section_path"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -k "inner_node or appendix_with_own or section_path or cross_chapter" -v`
Expected: FAIL — `KeyError: 'section_path'`

- [ ] **Step 3: 实现**

```python
def _build_section_path(stack: list[tuple[int, str, str]]) -> str:
    """由祖先链构建面包屑快照：`"6 混凝土分项工程 > 6.1 模板"`。

    R3 的 0 段已在收集阶段排除，故此处无需再过滤。无祖先返回空串
    （**不带尾随分隔符**——详情弹窗会直接拼接该串）。
    """
    return " > ".join(f"{no} {t}".strip() for _, no, t in stack)
```

第二遍中，仅当 `own_body` 为真才产出 clause，并把 `section_path` 写入其 dict：

```python
                "section_path": _build_section_path(ancestor_stack),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -v`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "feat: 内节点判据（有无自身正文）+ section_path 栈式构建

内节点只作祖先不入库；有自身正文的附录（CJJ2 附录A 35K 字符）作为叶条文
正常入库（R11）。section_path 为编号+标题的面包屑快照，无祖先时为空串，
同时修掉同级父级被提前 pop 与跨章泄漏。"
```

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
    """三者一律打标保留（clause_is_non=1），不整段丢弃"""
    md = "公告\n\n关于发布行业标准……\n"
    results = parse_markdown(md)
    assert len(results) == 1
    assert results[0]["is_non_clause"] is True
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
  1. clause_count          条文数
  2. content_chars         全部 content 的字符总数（守恒断言的分母）
  3. fake_clause_no_count  不含数字的条文号个数（伪条文号，目标：12 以内）
  4. breadcrumb_coverage   section_path 非空且段数 == level-1 的条文占比
  5. missing_sections      被引用却找不到标题的节号（量 Task 3 的残余缺口）

用法：python scripts/survey_structure.py <md路径> [--json]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parser.md_parser import parse_markdown  # noqa: E402


def survey_structure(md_text: str) -> dict:
    clauses = parse_markdown(md_text)
    fake = [c for c in clauses if not re.search(r'\d', c["clause_no"])]

    complete = 0
    for c in clauses:
        segs = [s for s in (c.get("section_path") or "").split(" > ") if s]
        if segs and len(segs) == c["level"] - 1:
            complete += 1
    coverage = complete / len(clauses) if clauses else 0.0

    # 残余缺口：条文编号的上一级节点号（如 18.3.1 → 18.3）在祖先链里找不到
    present = {c["clause_no"] for c in clauses}
    missing: set[str] = set()
    for c in clauses:
        parts = c["clause_no"].split('.')
        if len(parts) < 2:
            continue
        parent_no = ".".join(parts[:-1])
        if parent_no in present:
            continue
        if not any(f"{parent_no} " in s for s in (c.get("section_path") or "").split(" > ")):
            missing.add(parent_no)

    return {
        "clause_count": len(clauses),
        "content_chars": sum(len(c["content"]) for c in clauses),
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
| `content_chars` | 待填（旧实现 444,976） | **不得减少**（守恒） |
| `fake_clause_no_count` | 待填（旧实现 124） | **明显下降**（目标 ≤ 12） |
| `breadcrumb_coverage` | 待填 | 提升；缺口清单用于决定 Task 3 去留 |
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

临时把 Task 3 的次分组单元处理改为「整段丢弃」（模拟内容丢失），跑断言：

```bash
# 临时改 app/parser/md_parser.py：把 is_non_level_group_title 判定分支的
# `current_content_lines.append(title); continue` 改为 `continue`（丢标签且
# 让该段内容按原路径继续）——此处用最直接的模拟：把 Task 4 的裸编号项
# `if '.' not in clause_no: return None` 改为 `return None` 之外的丢弃分支。
# 任一「内容被吞」的变异都必须让 test_content_is_conserved 失败。
D:/Python/python.exe -m pytest tests/test_parse_conservation.py -v
```
Expected: **FAIL** — 断言必须报出字符数下降。**验证完成后立即还原改动**（`git checkout app/parser/md_parser.py`）。

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

模板及其支架应根据工程结构形式进行设计。

### 6.2 钢筋

#### 6.2.1 原材料

钢筋进场时应抽取试件作屈服强度检验。
"""


def _labels(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in parse_markdown(md):
        _scores, best, _ids = classify_clause(c["content"], c.get("parent_path", []),
                                              RULES, synonyms=[])
        out[c["clause_no"]] = best.get("dim4", "")
    return out


def test_classification_inputs_include_ancestor_titles():
    """固定事实：祖先标题确实进入分类匹配文本（这是耦合的机制依据）"""
    labels = _labels(MD)
    # 6.1.1 正文没有「模板」二字，但父链有「模板」→ 仍应命中 dim4=模板
    assert labels["6.1.1"] == "模板"
    assert labels["6.2.1"] == "钢筋"


def test_classification_labels_stable_against_baseline():
    """基线：与改造后实测一致。**改造若不改变本用例，说明层级未影响分类**；
    若改变，必须在此显式更新并说明理由（不得静默改）。"""
    assert _labels(MD) == {
        "6.1.1": "模板",
        "6.2.1": "钢筋",
    }
```

- [ ] **Step 2: 跑测试确认失败（当前分类输入含被污染的父链）**

Run: `D:/Python/python.exe -m pytest tests/test_classify_baseline.py -v`
Expected: FAIL — 至少一条标签不符（改造前的父链含被误判为标题的正文句）

- [ ] **Step 3: 修正期望值并记录差异**

把实际得到的标签写入断言，**并在提交信息中列出「哪些条文的标签因层级修复而变化、为什么**。若某条变化不符合预期（例如因父链污染消失导致原本命中的规则不再命中），需判断是修复收益还是回归。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_classify_baseline.py -v`
Expected: PASS

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

## Self-Review

**1. Spec coverage**（对照 spec §4.1 的 ①–⑧ 与本批范围）：

| spec 项 | 覆盖它的 Task |
|---|---|
| ① 层级只由编号点数推导 | Task 1 |
| ② `X.0.Y` 按 R3 处理 | Task 2 |
| ③ 内节点判据「有无自身正文」 | Task 6 |
| ④ R14 兄弟多数表决（两遍解析） | Task 5 |
| ⑤ `section_path` 栈式构建 | Task 6 |
| ⑥ 目次优先（TOC-first） | **不在本批**——已按 D6.2 降为证据触发（见下） |
| ⑦ 三个小修复 | Task 1 |
| ⑧ 非条文块名单扩充 | Task 7 |
| §6 三项回归指标 | Task 8 |
| **CEO 新增：守恒断言** | Task 9 |
| **CEO 新增：分类回归基线** | Task 10 |
| **CEO 新增：面包屑覆盖率** | Task 8 |
| **CEO 新增：重复条文号诊断** | Task 11 |
| **R7 次分组单元内容回流**（评审新查明，spec 未写） | Task 3 + Task 4 |

**2. Placeholder scan**：本计划无 TBD/TODO；两处「待填」是**必须现场实测**的基线数值（Task 8 Step 5 / Task 9 Step 2），已写明填入时机且「未填不得进入下一 Task」。

**3. Type consistency**：`_level_from_clause_no` / `_has_zero_segment` / `is_non_level_group_title` / `_RawLine` / `_vote_title_mode` / `_build_section_path` / `survey_structure` 的签名在定义处与消费处一致；`section_path` 键名在 Task 6 定义、Task 8 消费（`c.get("section_path")`）一致。

**4. 未纳入本批的项**（明确列出，避免被误认为遗漏）：
- **⑥ 目次优先**：D6.2 已决议降为**证据触发**——批一交付后读 `breadcrumb_coverage` 与 `missing_sections`，缺口大才启用。**Task 8 的 `missing_sections` 就是那条证据。**
- `parent_path` 的保留/改名、`section_path` 格式规范入文档、`models.Clause` 同步：属批二（Task 12）与文档收尾。
- `_should_emit_clause` 的接口与既有 `is_non_clause`/`is_cover_clause` 语义不变。
