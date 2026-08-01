from app.parser.md_parser import parse_markdown

SAMPLE_MD = """# GB 50204-2015 混凝土结构工程施工质量验收规范

## 5 混凝土分项工程

### 5.1 模板

#### 5.1.1 一般规定

模板及其支架应根据工程结构形式、荷载大小、地基土类别、施工设备和材料供应等条件进行设计。

#### 5.1.2 模板安装

模板安装应满足下列要求：

1. 模板的接缝不应漏浆；
2. 模板与混凝土的接触面应清理干净。

### 5.2 钢筋

#### 5.2.1 原材料

钢筋进场时，应按国家现行相关标准的规定抽取试件作屈服强度、抗拉强度、伸长率、弯曲性能和重量偏差检验。

## 6 预应力分项工程

### 6.1 预应力材料

预应力筋进场时，应按国家现行相关标准抽取试件作抗拉强度、伸长率检验。
"""

def test_parse_markdown_returns_list():
    results = parse_markdown(SAMPLE_MD)
    assert isinstance(results, list)
    assert len(results) > 0

def test_parse_markdown_extracts_clause_no():
    results = parse_markdown(SAMPLE_MD)
    clause_nos = [r["clause_no"] for r in results]
    assert "5.1.1" in clause_nos
    assert "5.2.1" in clause_nos

def test_parse_markdown_extracts_title():
    results = parse_markdown(SAMPLE_MD)
    titles = {r["clause_no"]: r["title"] for r in results}
    assert titles["5.1.1"] == "一般规定"
    assert titles["5.1.2"] == "模板安装"

def test_parse_markdown_extracts_content():
    results = parse_markdown(SAMPLE_MD)
    for r in results:
        if r["clause_no"] == "5.2.1":
            assert "屈服强度" in r["content"]
            break

def test_parse_markdown_parent_inheritance():
    results = parse_markdown(SAMPLE_MD)
    for r in results:
        if r["clause_no"] == "5.1.1":
            path = r.get("parent_path", [])
            assert len(path) >= 2
            assert any("混凝土" in p for p in path)
            assert any("模板" in p for p in path)
            break

def test_parse_markdown_handles_empty():
    results = parse_markdown("")
    assert results == []

def test_parse_markdown_levels():
    results = parse_markdown(SAMPLE_MD)
    levels = {r["clause_no"]: r["level"] for r in results}
    assert levels["5.1.1"] == 4
    assert levels.get("5.1") is None  # 中间标题不生成条文


# ═══════════════════════════════════════════
# 非 # 前缀编号行识别（extract_text / PaddleOCR-VL 纯文本结构）
# ═══════════════════════════════════════════

def test_parse_non_hash_numbered_clauses():
    """无 # 前缀的纯数字编号行应被识别为条文"""
    md = """1.0.1  为在混凝土结构中使用钢筋机械连接，制定本规程。

1.0.2  本规程适用于受力钢筋机械连接接头的设计、应用与验收。
"""
    results = parse_markdown(md)
    clause_nos = [r["clause_no"] for r in results]
    assert "1.0.1" in clause_nos
    assert "1.0.2" in clause_nos
    # 正文型编号行：编号后文本进入 content，标题为空
    r = results[0]
    assert r["content"] == "为在混凝土结构中使用钢筋机械连接，制定本规程。"
    assert r["title"] == ""


def test_parse_letter_numbered_clauses():
    """带字母的编号（D.4）应正确分离编号与标题"""
    md = """D.4 疏浚、吹填工程

D.4.1 开挖深度 20 m 及以上的岸坡开挖工程。
"""
    results = parse_markdown(md)
    # D.4 是标题，D.4.1 是更细的条文
    nums = [r["clause_no"] for r in results]
    assert any(n.startswith("D.4") for n in nums)
    for r in results:
        if r["clause_no"] == "D.4":
            assert r["title"] == "疏浚、吹填工程"
            assert "D.4" not in r["title"]


def test_parse_appendix_clauses():
    """附录编号应被识别"""
    md = """附录A 接头型式检验的加载制度

A.1 检验设备

A.1.1 加载装置应满足要求。
"""
    results = parse_markdown(md)
    nums = [r["clause_no"] for r in results]
    assert any(n.startswith("A.") for n in nums)
    # 附录/章节标题作为 parent_path 继承，不生成独立条文
    for r in results:
        path = r.get("parent_path", [])
        assert any("接头型式检验的加载制度" in p for p in path)
        assert any(p == "检验设备" for p in path)


def test_extract_text_style_plain_text():
    """模拟 extract_text 输出的纯文本结构（JGJ107 风格）应能解析出多条条文"""
    # 这是 JGJ107 extract_text 输出的代表性结构：
    # 一级章节无 # 前缀，条文号与正文同行
    md = """1  总    则

1.0.1  为在混凝土结构中使用钢筋机械连接，做到技术先进、安全适用、经济合理、确保质量，制定本规程。

1.0.2  本规程适用于房屋与一般构筑物中受力钢筋机械连接接头的设计、应用与验收。

2  术语、符号

2.1  术    语

2.1.1  钢筋机械连接
通过钢筋与连接件的机械咬合作用，将一根钢筋中的力传递至另一根钢筋的连接方法。
"""
    results = parse_markdown(md)
    assert len(results) > 0
    nums = [r["clause_no"] for r in results]
    assert "1.0.1" in nums
    assert "2.1.1" in nums
    # 章节编号不应作为独立条文（无自身正文）
    assert not any(n in ("1", "2", "2.1") for n in nums)


def test_toc_lines_filtered():
    """目录行（编号后跟点线+页码）不应被识别为条文标题"""
    md = """1  总    则....................................................... 6

2  术语、符号..................................................... 6

1.0.1  为在混凝土结构中使用钢筋机械连接，制定本规程。
"""
    results = parse_markdown(md)
    nums = [r["clause_no"] for r in results]
    # 目录行被过滤，只应识别出 1.0.1
    assert "1.0.1" in nums
    assert not any(n in ("1", "2") for n in nums)
    # 目录行的标题不应残留点线
    for r in results:
        assert "...." not in r.get("title", "")


def test_multi_space_title_cleanup():
    """多空格标题应清理（1  总    则 → 总则）"""
    md = """1  总    则

1.0.1  条文内容。
"""
    results = parse_markdown(md)
    for r in results:
        if r["clause_no"] == "1.0.1":
            path = r.get("parent_path", [])
            assert any(p == "总则" for p in path)
            break
    # 直接验证标题提取
    from app.parser.md_parser import _clean_title
    assert _clean_title("总    则") == "总则"
