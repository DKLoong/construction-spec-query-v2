from app.parser.md_parser import (
    parse_markdown, is_non_clause_title, is_cover_clause, _should_emit_clause,
    _is_zero_segment_node,
)

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
    # 层级 = 1 + 编号点数（唯一尺子）；5.1.1 两个点 → 3
    assert levels["5.1.1"] == 3
    assert levels.get("5.1") is None  # 中间标题不生成条文（无自身正文 → 内节点）


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
    """多空格标题应清理（1.1  总    则 → 总则）

    ⚠ 夹具偏离计划：原为裸编号 `1  总    则`。Task 4 起裸编号行不再是候选行
    （R7：`1 总则` 与条内的「项」`1 钢筋` 同形，无法区分），故该行既不进
    title_stack 也不再是 1.0.1 的父级 → 原夹具在 Task 4 后必失败。改用带点的
    `1.1`（层级 2 < 1.0.1 的 3，父链语义不变），继续覆盖「非 # 前缀标题型行的
    _clean_title(tail)」这条路径；断言与子条文号均未改动。
    """
    md = """1.1  总    则

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


# ═══════════════════════════════════════════
# 非条文黑名单判定（前言/目次/条文说明/用词说明）
# ═══════════════════════════════════════════

def test_is_non_clause_title_exact_hits():
    """精确命中黑名单的标题都应判定为非条文"""
    for title in ["前言", "目次", "Contents", "条文说明", "本规程用词说明", "本规范用词用语说明"]:
        assert is_non_clause_title(title), f"{title} 应命中黑名单"


def test_is_non_clause_title_substring_prefix_hits():
    """子串/前缀兜底：前言前缀、条文说明子串、用词说明后缀"""
    assert is_non_clause_title("前言部分")
    assert is_non_clause_title("3.0.2 条文说明…")
    assert is_non_clause_title("本规程用词说明")
    # 按设计变更（Task 7）：用词说明类改按**法定名称结尾**匹配，旧的
    # `startswith(本规程用词说明/本规范用词用语说明)` 前缀兜底被替换。
    # 带尾随限定语的写法（…的补充）不再命中；实测语料 0 例（见本次提交信息）。
    assert not is_non_clause_title("本规范用词用语说明的补充")
    # 尾随限定语不再命中，但法定名称与「前缀 + 名称」变体一律命中
    assert is_non_clause_title("本规范用词用语说明")
    assert is_non_clause_title("标准用词说明")


def test_is_non_clause_title_normal_not_marked():
    """正常条文标题不应误标"""
    assert not is_non_clause_title("总则")
    assert not is_non_clause_title("模板设计")
    assert not is_non_clause_title("原材料")
    assert not is_non_clause_title("混凝土分项工程")
    assert not is_non_clause_title("")
    assert not is_non_clause_title(None)


def test_parse_toc_filtered_not_generated():
    """目次/Contents 直接过滤，不生成 clause"""
    md = """## 目次
1 总则 ..................................................... 1
2 术语、符号 ..................................................... 6

## 1 总则

1.0.1  正文内容。
"""
    results = parse_markdown(md)
    # 不应出现目次标题对应的 clause
    assert not any(r.get("title") == "目次" for r in results)
    # 正常条文仍应生成
    assert any(r["clause_no"] == "1.0.1" for r in results)


def test_parse_contents_filtered_not_generated():
    """英文 Contents 直接过滤，不生成 clause；后续中文条文正常解析"""
    md = """## Contents
1 General Provisions .......... 1

## 1 总则

1.0.1  正文内容。
"""
    results = parse_markdown(md)
    assert not any(r.get("title") == "Contents" for r in results)
    assert any(r["clause_no"] == "1.0.1" for r in results)
    # 目次段内的一切内容一律丢弃（`discard_section`）。`Contents` 无中文：若中文检查
    # 排在目次过滤之前，它会被提前 continue 掉、`discard_section` 永不置位，
    # 目录行便漏进后一条正文（此处是「1 总则」的 content）。
    for r in results:
        assert "General Provisions" not in r["content"]


def test_parse_qianyan_retained_and_marked():
    """前言保留进库，打标 is_non_clause=True"""
    md = """## 前言

本规范为适应混凝土结构工程发展的需要而编制。

## 1 总则

1.0.1  正文内容。
"""
    results = parse_markdown(md)
    qianyan = [r for r in results if r.get("title") == "前言"]
    assert len(qianyan) == 1
    assert qianyan[0]["is_non_clause"] is True
    # 正常条文不打标
    for r in results:
        if r["clause_no"] == "1.0.1":
            assert r["is_non_clause"] is False


def test_parse_tiaowenshuoming_body_rows_inherit():
    """条文说明段内的正文型编号行继承 is_non_clause=True"""
    md = """## 条文说明

3.0.1  本条根据工程实践经验制定。

3.0.2  本条说明材料进场检验要求。
"""
    results = parse_markdown(md)
    assert len(results) >= 2
    for r in results:
        assert r["is_non_clause"] is True, f"条文说明段内 {r['clause_no']} 应打标"


def test_parse_tiaowenshuoming_numbered_title_retained():
    """「3.0.2 条文说明」这类标题本身也打标保留"""
    md = """3.0.2  条文说明

本条说明的内容。
"""
    results = parse_markdown(md)
    assert len(results) >= 1
    assert results[0]["is_non_clause"] is True
    assert "条文说明" in results[0]["title"]


def test_parse_normal_clause_has_is_non_clause_false():
    """普通条文默认 is_non_clause=False（新字段默认值）"""
    md = """## 1 总则

1.0.1  正文内容。
"""
    results = parse_markdown(md)
    assert len(results) >= 1
    for r in results:
        assert r["is_non_clause"] is False


def test_announcement_and_reference_list_are_non_clause():
    """R8/R8b：公告 / 引用标准名录 是 182 号第六、七条的法定名称

    实测 spec20（CJJ2）有 2 条公告伪条文，引用标准名录的列表项被误吞成
    clause_no='1'/'10'。

    ⚠ 上段是 brief 原文，实测两处**已过期**（Task 1/4 之后不再成立，本 Task 订正）：
      ① 「伪条文」出自旧 `_extract_clause_no` 的 `return title` 兜底，Task 1 已删；
         CJJ2 的两条公告现在是**整段丢弃**（不是成伪条文）。其标题不等于「公告」，
         故本用例的**精确命中**覆盖不到——已由 `_LEGAL_NAME_SUFFIXES` 的后缀匹配修正，
         解析侧钉在 test_announcement_variants_from_cjj2_are_non_clause。
      ② 引用标准名录的列表项（`1 《…》GB 50010`）自 Task 4 起被裸数字判据排除，
         不再是 clause_no='1'/'10'，而是作为**纯文本**并进上一条的 content。
    本用例只断言谓词，不断言上述解析行为（解析侧证据见本次提交信息与 task-7-report）。
    """
    assert is_non_clause_title("公告") is True
    assert is_non_clause_title("引用标准名录") is True

def test_announcement_variants_from_cjj2_are_non_clause():
    """R8：CJJ2 的两条**真实**公告标题按法定名称后缀命中，且打标保留、不整段丢弃。

    标题逐字照抄 `data/outputs/f543577f/f543577f.md:58` 与 `:62`。二者都带前缀/限定语，
    「精确命中 `公告`」两者全漏（实测修复前这两条公告块连同批准正文被整段丢弃）。
    同组复验反过匹配：后缀匹配不得把「公告发布要求」判成非条文。
    """
    t1 = "中华人民共和国住房和城乡建设部 公告"
    t2 = "关于发布行业标准《城市桥梁工程施工与质量验收规范》的公告"
    assert is_non_clause_title(t1) is True
    assert is_non_clause_title(t2) is True
    assert is_non_clause_title("公告发布要求") is False

    md = (
        "# 中华人民共和国住房和城乡建设部 公告\n"
        "\n"
        "第140号\n"
        "\n"
        "## 关于发布行业标准《城市桥梁工程施工与质量验收规范》的公告\n"
        "\n"
        "现批准《城市桥梁工程施工与质量验收规范》为行业标准，编号为 CJJ2-2008。\n"
        "\n"
        "### 前言\n"
        "\n"
        "前言正文。\n"
    )
    results = parse_markdown(md)
    got = {c["clause_no"]: c for c in results}
    # 两条公告都成为 is_non_clause=1 的条（而非整段丢弃）
    assert t1 in got and got[t1]["is_non_clause"] is True
    assert t2 in got and got[t2]["is_non_clause"] is True
    # 公告正文随块保留（证明「打标保留」而非「直接过滤」）
    assert "第140号" in got[t1]["content"]
    assert "现批准" in got[t2]["content"]
    # 紧随的 `### 前言` 照旧打标（不改变既有语义）
    assert got["前言"]["is_non_clause"] is True


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

    夹具两处偏离 brief（证据见本次提交信息与 task-7-report）：
      ① `公告` 必须写成 `# 公告`：裸行不是候选行（`_match_clause_line` 要求编号模式），
         谓词再对也无法产出 clause；语料里公告块本就带 `#`（fd4e78c5:23）。
         裸行支持属「routed observation 2」，控制器保留裁定，本 Task 不动。
      ② 末尾要有一条**标题型**行（`## 1 总则`）：正文型编号行会继承上一段
         `inherit_non_clause`（Task 3 钉死的语义），缺它则 `1.0.1` 也被打标 → `len(r) == 2`。
    """
    md = "# 公告\n\n关于发布行业标准……\n\n## 1 总则\n\n1.0.1 正文甲。\n"
    results = parse_markdown(md)
    r = [c for c in results if c["is_non_clause"]]
    assert len(r) == 1 and r[0]["clause_no"] == "公告"
    assert "关于发布行业标准" in r[0]["content"]

def test_toc_section_is_discarded_entirely():
    """目次 / Contents 属「直接过滤」类：段内**一切丢弃**，不进库。

    这是既有行为（`_NON_CLAUSE_FILTER_TITLES` + `discard_section`），本批必须原样保留
    （工程评审 C4：重写若丢掉它，目录行会泄漏进条文正文，且批二 Task 13 的
    tooltip 文案「不含目次」就成了假话）。

    ⚠ brief 的裸 `目次` 夹具对这一机制**零保护**：裸行不是候选行，`discard_section`
    根本不置位，目录行只是恰好被「空栈 flush」丢掉。变异探针（把
    `is_filter_non_clause_title` 改成恒 False）下，裸夹具**仍然通过**。故补跑 `#` 形式。
    """
    md = "目次\n\n1 总则 ..... 1\n\n2 术语 ..... 3\n\n1.0.1 正文甲。\n"
    results = parse_markdown(md)
    assert all(c["clause_no"] != "目次" for c in results)
    assert all("总则 ..... 1" not in c["content"] for c in results)
    assert any(c["clause_no"] == "1.0.1" for c in results)

    # `#` 形式才真正走到 `discard_section`：目录行夹在前后条文之间，不会被误并
    md_hash = "1.0.1 正文甲。\n\n## 目次\n\n1 总则 ..... 1\n\n2 术语 ..... 3\n\n1.0.2 正文乙。\n"
    results_hash = parse_markdown(md_hash)
    assert all(c["clause_no"] != "目次" for c in results_hash)
    assert all("总则 ..... 1" not in c["content"] for c in results_hash)
    assert any(c["clause_no"] == "1.0.2" for c in results_hash)


# ═══════════════════════════════════════════
# 全角点号兼容（U+FF0E，OCR 高频错误）
# ═══════════════════════════════════════════

def test_fullwidth_dot_title_clause_parsed():
    """全角点号标题型编号行应被解析并归一化为半角"""
    md = """5．2．1 原材料

钢筋进场时，应按国家现行标准检验。
"""
    results = parse_markdown(md)
    assert len(results) >= 1
    r = results[0]
    assert r["clause_no"] == "5.2.1"
    assert r["title"] == "原材料"
    assert "钢筋进场时" in r["content"]


def test_fullwidth_dot_body_clause_parsed():
    """全角点号正文型编号行（长句）应被解析，标题为空"""
    md = """5．2．1  为在混凝土结构中使用钢筋机械连接，做到技术先进、安全适用、经济合理、确保质量，并制定本规程。
"""
    results = parse_markdown(md)
    assert len(results) >= 1
    r = results[0]
    assert r["clause_no"] == "5.2.1"
    assert r["title"] == ""
    assert "钢筋机械连接" in r["content"]


def test_fullwidth_dot_level_inference():
    """全角点号归一化为半角后按点数计层级（唯一尺子：1 + 点数）；1.0.1 → 3"""
    md = """1．0．1  正文内容。

1．0．2  更多内容。
"""
    results = parse_markdown(md)
    assert len(results) == 2
    for r in results:
        assert r["clause_no"] == r["clause_no"].replace("．", ".")
        assert r["level"] == 3


def test_fullwidth_dot_hash_title():
    """# 前缀标题行中的全角点号也应被提取并归一化"""
    md = """## 5．2．1 原材料

钢筋进场时检验。
"""
    results = parse_markdown(md)
    assert len(results) >= 1
    r = results[0]
    assert r["clause_no"] == "5.2.1"
    assert r["title"] == "原材料"


def test_fullwidth_dot_appendix():
    """全角点号附录编号应被识别（附录A．1）"""
    md = """附录A　接头型式检验的加载制度

A．1.1　加载装置应满足要求。
"""
    results = parse_markdown(md)
    nums = [r["clause_no"] for r in results]
    assert any(n.startswith("A.1") for n in nums)


# ═══════════════════════════════════════════
# 空内容条文过滤
# ═══════════════════════════════════════════

def test_should_emit_clause_filters_fully_empty():
    """完全空条文（无标题无正文）→ 不生成"""
    assert _should_emit_clause("", "") is False
    assert _should_emit_clause("", "   ") is False
    assert _should_emit_clause(None, None) is False


def test_should_emit_clause_keeps_title_only():
    """有标题但 content 空的章节 → 保留（正常行为，勿删）"""
    assert _should_emit_clause("总则", "") is True
    assert _should_emit_clause("总则", "   ") is True


def test_should_emit_clause_keeps_content_only():
    """无标题有正文 → 保留"""
    assert _should_emit_clause("", "正文内容") is True


def test_parse_never_emits_fully_empty_clause():
    """parse_markdown 不生成「无标题且无正文」的完全空条文，正常条文不受影响"""
    md = """## 1 总则

## 2 术语

2.1.1  正文内容。
"""
    results = parse_markdown(md)
    for r in results:
        assert not (not r["title"] and not r["content"])
    assert any(r["clause_no"] == "2.1.1" for r in results)


# ═══════════════════════════════════════════
# 封面脏数据判定（is_cover_clause）
# ═══════════════════════════════════════════

def test_is_cover_clause_standard_cover():
    """标准首页（ICS/中华人民共和国国家标准/代替 GB/T）判定为封面"""
    content = """ICS 77.140.60

中华人民共和国国家标准

GB/T 1499.1—2017

代替 GB/T 1499.1—2008"""
    assert is_cover_clause(content) is True


def test_is_cover_clause_publication_page():
    """出版信息页（中国标准出版社/出版发行/定价）判定为封面"""
    content = """中国标准出版社出版发行

地址：北京市朝阳区和平里西街甲2号

定价：45.00 元"""
    assert is_cover_clause(content) is True


def test_is_cover_clause_normal_clause_not_cover():
    """正常条文（含「实施」「发布」但不含特征词组合）不判定为封面"""
    content = """本标准由住房和城乡建设部负责管理，由本规程编制组负责具体技术内容的解释。

本规程自发布之日起实施。"""
    assert is_cover_clause(content) is False


def test_is_cover_clause_single_keyword_not_cover():
    """单个特征词命中（如只出现「印刷」）不判定为封面（需命中 ≥2 个）"""
    content = "本规范采用胶版印刷工艺装订。"
    assert is_cover_clause(content) is False


# ═══════════════════════════════════════════
# 层级单一尺子 + 编号识别三个小修复（Task 1）
# ═══════════════════════════════════════════

def test_level_comes_from_number_not_hash_count():
    """层级只由编号点数推导，与井号数量无关

    井号数取 3 与 4：两者都构成 Markdown 标题（`#{1,6}`），旧尺子下层级为 3/4、
    新尺子下同为 3。**井号超过 6 的行不是标题**（`#{1,6}` 匹配不到），不能用来
    表达「井号数不同而层级相同」。
    """
    md = "### 5.1.1 一般规定\n\n内容甲。\n\n#### 5.1.2 模板安装\n\n内容乙。\n"
    levels = {r["clause_no"]: r["level"] for r in parse_markdown(md)}
    assert levels["5.1.1"] == 3
    assert levels["5.1.2"] == 3   # 井号数不同，层级相同


def test_hash_heading_without_clause_no_is_not_clause():
    """# 路径匹配不到编号即不当条文（旧实现兜底 return title 会造出伪条文号）"""
    md = "# Code for construction and quality acceptance of bridge works\n\n正文。\n"
    assert parse_markdown(md) == []

def test_bare_year_is_not_clause_no():
    """裸露 4 位年份不得成为条文号（`## 2008 年发布公告` 不产出条文）

    夹具必须走 `#` 路径——`_BARE_YEAR` 守卫只在 `#` 路径上：裸行 `2008` 会先被
    `_match_clause_line` 的「无中文」规则排除，`parse_markdown` 返回 `[]`，
    断言便遍历空列表、恒真（空断言）。末行 `1.0.1` 保证 results 非空。
    """
    md = "## 2008 年发布公告\n\n正文内容。\n\n1.0.1 正文。\n"
    results = parse_markdown(md)
    assert results  # 夹具须产出条文，否则下面的断言是空断言
    assert all(r["clause_no"] != "2008" for r in results)

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


# ═══════════════════════════════════════════
# R3：章内不分节时条编号的节位为 0（Task 2）
# ═══════════════════════════════════════════

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


# ═══════════════════════════════════════════
# 裸编号项不作为条文（Task 4）
# ═══════════════════════════════════════════

def test_bare_numbered_item_is_not_a_clause():
    """裸编号项（无点号，如 `1`、`6`）不是条文，是所属条内部的「项」"""
    md = "5.1.1 模板安装应满足下列要求：\n\n1 模板的接缝不应漏浆；\n\n2 接触面应清理干净。\n"
    results = parse_markdown(md)
    nos = [c["clause_no"] for c in results]
    assert nos == ["5.1.1"]          # 裸 1/2 不成条文
    assert "模板的接缝" in results[0]["content"]
    assert "接触面应清理干净" in results[0]["content"]

def test_bare_numbered_short_title_is_still_not_a_clause():
    """短标题形态的裸编号项同样不成条文（旧实现按 ≤20 字会误判为标题型）

    ⚠ 夹具偏离计划：计划给的 `"5.1.1 一般规定\\n\\n1 钢筋\\n\\n2 水泥\\n"` 在本
    Task 的检查点上**恒返回 `[]`**（旧循环只在缓冲非空时结算，三条标题型行的
    条目从未被结算）→ 原断言是空断言（`assert nos` 可证）。故每条编号行后补一句
    正文，使夹具产出条文、断言可证伪。Task 3 无条件结算后原夹具才会变为有效。
    """
    md = "5.1.1 一般规定\n\n正文甲。\n\n1 钢筋\n\n正文乙。\n\n2 水泥\n\n正文丙。\n"
    nos = [c["clause_no"] for c in parse_markdown(md)]
    assert nos   # 夹具须产出条文，否则下面的断言是空断言
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


# ═══════════════════════════════════════════
# 解析主循环：R14 投票 / 无条件 flush / 内节点判据 / section_path（Task 3）
# ═══════════════════════════════════════════

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

def test_vote_only_confirms_title_never_promotes_long_tail():
    """R14 投票只用于**确认**标题：组内多数判「带标题」时，仍要求该行自身也像标题。

    ⚠ 可证伪点（本用例即为守卫的钉）：把主循环的 `is_titled` 换成
    `title_mode.get(...)`（去掉 `and _looks_like_title(tail)`）后，下面这条
    `14.4 <整句>` 会因组内多数（3 条短标题 vs 1 条长句）被判「带标题」→ 长句成为
    title 且无自身正文 → 按内节点判据**整条不入库**、文本消失
    （RED 实测：`assert "14.4" in clauses` 失败，`clauses` 只有 14.1~14.3）。
    实测规模（CJJ2）：此机制吃掉**688 字符 plain / 12 条**消失——条文说明章的
    `13.5 <整句>` 与正文的 `13.5 悬臂拼装` 同组（组内 n=9、yes=7）。
    方向恒为**保内容**：这一「与」只会**减少**标题型判定。
    """
    md = ("14.1 制造\n\n钢梁应在工厂内焊接制造。\n\n"
          "14.2 现场安装\n\n现场安装应符合下列规定。\n\n"
          "14.3 检验标准\n\n检验标准应符合本规范规定。\n\n"
          "14.4 顶推施工适用于跨径 40～60m 预应力混凝土等截面（等高）连续梁架设。\n")
    results = parse_markdown(md)
    clauses = {c["clause_no"]: c for c in results}
    # 组内多数确实判「带标题」——否则本用例不构成对守卫的覆盖（判无标题时 title 为 ""）
    assert clauses["14.1"]["title"] == "制造"
    assert clauses["14.3"]["title"] == "检验标准"
    # 长句行不得被「确认」为标题：必须产出，且文本留在 content 里
    assert "14.4" in clauses
    assert clauses["14.4"]["title"] == ""
    assert "顶推施工适用于跨径 40～60m" in clauses["14.4"]["content"]
    assert not any("顶推施工适用于跨径" in c["title"] for c in results)

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
    # 页标记自身保留但隐藏（is_non=1），前引文字归它，不污染真实条文
    page = [c for c in clauses.values() if c["clause_no"] == "第1页"]
    assert len(page) == 1 and page[0]["is_non_clause"] is True
    assert "ICS" in page[0]["content"]
    # 首条真条文不得带上封面特征词（否则会被 _filter_cover_clauses 误删）
    # ⚠ 断言偏离计划：计划写的 `clauses["1"]["content"].strip() == ""` 与本 Task 的
    #    内节点判据**互斥**——`## 1 总则` 无自身正文，按组 3 判据不入库，该键直接
    #    `KeyError: '1'`（实测）。改为同一命题的更强形式：`1` 根本不成条文
    #    （封面文字既没并进它、也没并进 `1.0.1`），且**任何**非页标记条文都不带封面词。
    assert "1" not in clauses
    real = [c for c in clauses.values() if c["clause_no"] != "第1页"]
    assert real                                        # 防空断言：必须有真条文
    assert all(is_cover_clause(c["content"]) is False for c in real)
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
    # ⚠ 断言偏离计划：计划写的 `clauses["14.3.2"]["content"].strip() == "正文乙。"`
    #    预设 14.3.2 是**标题型**行，但 `_looks_like_title` 把「以句末标点（含全角
    #    「：」）结尾」判为非标题，而本组 (层级 3, 父键 "14.3") 的两条 tail 均非标题
    #    → 投票判「无标题」→ 编号后文本进 content（旧实现同样按正文型处理，故此断言
    #    描述的并非既有行为）。改为断言其**语义意图**：分组内容只归 14.3.1、
    #    14.3.2 保留自有正文，两者互不串味。
    assert "正文乙。" in clauses["14.3.2"]["content"]
    assert "钢材的品种" not in clauses["14.3.2"]["content"]
    assert "检查数量" not in clauses["14.3.2"]["content"]
    assert clauses["14.3.2"]["title"] == ""

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


# ── 组 5：批一回归夹具 + 结构勘察（Task 8） ──
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
    # ⚠ 偏离计划：计划写的 `> 900` 是 Task 3 实施**前**的预估阈值（当时无实测值）。
    #    实测都低于 900——Task 3 后 895（task-3-report.md:228）、Task 7 后 898
    #    （task-7-report.md:79）；原样保留会让本测试恒定失败。故阈值下调到 850：
    #    只守「夹具完整 + 解析出全量规模条文」，不锁定精确值（精确基线由
    #    批一验收基线表与 Task 9 的守恒断言守）。
    assert stats["clause_count"] > 850
    # ⚠️ Task 13（裁定 R-T13-4）：覆盖率口径改为**只统计条文行**（非条文行的面包屑按
    #    `_build_section_path` 的契约本应为空），阈值**直接**收到 0.95——实测 0.9781。
    #    这里不走「先落 M14 的 0.85 再提高」：执行顺序表把 Task 13 排在 Task 12 之前，
    #    本 Task 落地的就是终值；0.85 对 0.9781 会放走 −0.13 的退化，门禁形同虚设。
    assert stats["breadcrumb_coverage"] >= 0.95
    # ⚠️ 偏离简报（fix round 1，裁定）：原断言 `isinstance(..., list)` **空表也过**，
    #    是 M14 点名的「恒真门禁」。换成**集合断言**（实测恰为这六项）。
    #    它同时是 **R3 护栏**：脚本里「`X.0.Y` 的节位为 0 → 该级不存在」的剔除若被删掉，
    #    `1.0` / `2.0` / `3.0` / `23.0` 会立刻进集合（实测），本断言随即变红。
    #    另注：`missing_sections` 口径已收窄为**只统计条文行**，且**判断父级是否已存在
    #    也只用条文行**（R-T13-5 fix round 2）——若后者退回全量口径，本集合会缩回
    #    `['10.7','11.5','8']`（段内注释行 `14`/`17.5`/`18.8` 把三个真缺口掩盖掉），同样变红。
    # ⏭️ **Task 14 会把本断言收紧为 `["10.7", "14", "17.5"]`**：这六项里 `14`/`17.5`/`18.8`
    #    的面包屑被伪 level-1 节点污染（实测 `14.3`→`'3'`、`17.5.8`→`'3'`、`18.8.9`→`'3'`），
    #    伪节点修好后 `18.8`/`8`/`11.5` 不再缺失。**本 Task 不抢跑**（不预先写入该值）。
    assert stats["missing_sections"] == ["10.7", "11.5", "14", "17.5", "18.8", "8"]


# ═══════════════════════════════════════════
# 组 6：条文说明段级规则（Task 13）
# ═══════════════════════════════════════════

# 段级规则的合成用例：段内**另起一章**（`# 2 基本规定`）是关键——它证明段级标志
# 穿透了与正文同形的章标题（靠继承做不到，见裁定 R-T13-1）。
_COMMENTARY_DOC = """# 1 总则
1.0.1 本条规定了适用范围与基本要求，并明确了与其他标准的衔接关系。

条文说明
# 1 总则
1.0.1 本条规定了适用范围的说明，供使用者参考，具体执行时以正文为准。

# 2 基本规定
2.0.1 本条说明了基本规定的编制依据与执行尺度，供使用者参考。
"""


def test_commentary_marker_opens_non_clause_region():
    """裸行 `条文说明` 开启延伸至文末的非条文段：段内**标题型**行同样打标。"""
    rows = parse_markdown(_COMMENTARY_DOC)
    assert [(r["clause_no"], r["is_non_clause"]) for r in rows] == [
        ("1.0.1", False),      # 段前：正文条文
        ("1.0.1", True),       # 段内：与正文同号同名的条文说明
        ("2.0.1", True),       # 段内**另起一章之后**仍打标 ← 本 Task 的核心
    ]


def test_commentary_marker_requires_exact_whole_line():
    """**整行精确匹配**是安全边界：非整行的行不得开启该段。

    反例集合含夹具真实行 `附：条文说明 ..... 247`（目次行，夹具行 231）与
    夹具行 6843（含「条文说明」的正文长句）——两者剥掉受控前缀后仍带点引号/页码
    或整句正文，故不命中。若改用 `is_non_clause_title` 作整行判据，6843 行会开启
    该段并吞掉其后正文（探针实测：`content_chars` 445,893 → 445,871）。
    """
    for line in ("条文说明如下", "附：条文说明 ..... 247", "见条文说明"):
        doc = ("# 1 总则\n"
               "1.0.1 本条规定了适用范围与基本要求。\n\n"
               f"{line}\n\n"
               "1.0.2 本条规定了检验方法与合格判定标准。\n")
        rows = parse_markdown(doc)
        tail = [r for r in rows if r["clause_no"] == "1.0.2"]
        assert tail, f"{line} 之后的正条文不应消失"
        assert tail[0]["is_non_clause"] is False, f"{line} 误开非条文段"


def test_numbered_commentary_marker_does_not_open_region():
    """⚠️ 偏离简报（fix round 1）：**编号**标记不开启段级规则。

    简报此处的反例集合原含 `"3.0.2 条文说明…"`，并断言其后条文 `is_non_clause is False`。
    实测该断言**与 Task 3 的既有、被钉住的语义冲突**，任何实现都过不了：
      ① `_candidate_of("3.0.2 条文说明…")` → `(3, '3.0.2', '条文说明')`（**是**候选行，
         `rstrip(' .…')` 把省略号剥掉）；
      ② 它是正文型行且 tail 命中 `is_non_clause_title` → `own_non=True` → `inherit_non_clause=True`
         → 其后**正文型**编号行继承打标（`test_parse_tiaowenshuoming_numbered_title_retained`
         与 `test_parse_tiaowenshuoming_body_rows_inherit` 明文钉住这一行为，本 Task 不许改，
         见裁定 R-T13-3）；
      ③ 该行**不**是本 Task 的段标记：`_is_commentary_marker("3.0.2 条文说明…")` 为 **False**。
    故把它的**正确形态**单列：编号标记只走「继承」，靠**标题型行重置基调**收口——
    与裸行标记的段级穿透形成判别（下例若把 `3.0.2 条文说明` 换成裸行 `条文说明`，
    `2.0.1` 会因段级规则打标；实测该判别有效）。
    """
    numbered = ("# 1 总则\n1.0.1 本条规定了适用范围与基本要求。\n\n"
                "3.0.2 条文说明\n\n"
                "# 2 基本规定\n\n2.0.1 本条规定了基本规定的编制依据。\n")
    rows = [r for r in parse_markdown(numbered) if r["clause_no"] == "2.0.1"]
    assert rows and rows[0]["is_non_clause"] is False, "编号标记不应开启段级规则"


def test_commentary_region_does_not_leak_backwards():
    """段级标志单向：不得回溯打标标记行**之前**的条文。"""
    rows = parse_markdown(_COMMENTARY_DOC)
    assert rows[0]["clause_no"] == "1.0.1" and rows[0]["is_non_clause"] is False


def test_commentary_prefix_variant_opens_region():
    """法定写法 `附：条文说明` 同样开段。

    受控前缀，与 `_LEGAL_NAME_SUFFIXES` 同一条设计原则（法定名称在真实文档里几乎
    总带前缀或限定语，只认精确值会整块漏判）。182 号正文即写作「附：条文说明」。
    """
    doc = ("# 1 总则\n1.0.1 本条规定了适用范围与基本要求。\n\n"
           "附：条文说明\n\n"
           "1.0.1 本条规定了适用范围的说明，供使用者参考。\n")
    rows = parse_markdown(doc)
    tail = [r for r in rows if r["clause_no"] == "1.0.1"]
    assert len(tail) == 2
    assert tail[0]["is_non_clause"] is False and tail[1]["is_non_clause"] is True


def test_cjj2_commentary_section_is_marked(cjj2_md):
    """端到端：CJJ2 的条文说明段必须被打标（本 Task 的立项目标）。

    实测 **168** 条（段前 4 条法定非条文块 + 段内 164 条）；阈值取 100 留重构余量。
    """
    marked = [c for c in parse_markdown(cjj2_md) if c["is_non_clause"]]
    assert len(marked) > 100, f"仅 {len(marked)} 条被打标——条文说明段仍未生效"
