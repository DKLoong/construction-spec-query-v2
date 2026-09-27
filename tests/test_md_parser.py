from app.parser.md_parser import (
    parse_markdown, is_non_clause_title, is_cover_clause, _should_emit_clause,
    _is_zero_segment_node, _extract_title, _candidate_of,
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
    """带字母的编号（D.4）应正确分离编号与标题

    ⚠ M1（Task 3 复核 Minor #1）：原实现断言的是 `clause_no == "D.4"` 的那一行，而
    `D.4` 是**内节点**（标题型且无自身正文 → 只作祖先、不入库），本夹具只产出 `D.4.1`，
    故那个 `if` 是**死分支** —— 「编号与标题分离」这条断言一次都没执行过（删掉它测试照绿）。
    实测（本 Task 探针）本夹具的唯一产出：
    `('D.4.1', title='', content='开挖深度 20 m 及以上的岸坡开挖工程。',
      parent_path=['疏浚、吹填工程'])`。
    故改为断言**真正落地的形态**：`D.4.1` 的父链携带的是**剥掉编号后**的纯标题
    （`疏浚、吹填工程`，不含 `D.4`）—— 分离若坏掉（`_extract_title` 退回原串），
    父链会变成 `['D.4 疏浚、吹填工程']`，本断言立刻变红。
    """
    md = """D.4 疏浚、吹填工程

D.4.1 开挖深度 20 m 及以上的岸坡开挖工程。
"""
    results = parse_markdown(md)
    # 先钉住夹具形态：下面两条断言的可证伪性依赖「D.4 不入库、D.4.1 入库」
    assert [r["clause_no"] for r in results] == ["D.4.1"], \
        f"夹具形态变了（D.4 是否入内节点、D.4.1 是否产出）：{[r['clause_no'] for r in results]}"
    assert results[0]["parent_path"] == ["疏浚、吹填工程"], \
        f"字母编号未与标题分离，父链标题应为纯标题：{results[0]['parent_path']!r}"
    # 直接钉住提取函数本身（父链走的正是同一个 `_extract_title`）
    assert _extract_title("D.4 疏浚、吹填工程") == "疏浚、吹填工程"
    assert "D.4" not in _extract_title("D.4 疏浚、吹填工程")


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
    # ⚠ M3（Task 3 复核 Minor #5）：原写法是 `for r in results: if ...: assert ...; break` ——
    #    夹具若不产出 `1.0.1`（如实现回归），循环体一次都不执行、父链断言被**静默跳过**、
    #    本用例照样变绿。故先钉住夹具产出，再取该行断言。
    hits = [r for r in results if r["clause_no"] == "1.0.1"]
    assert hits, f"夹具必须产出 1.0.1，否则下面的父链断言是空断言：{[r['clause_no'] for r in results]}"
    assert any(p == "总则" for p in hits[0]["parent_path"])
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


def test_reference_list_block_is_not_merged_into_previous_clause():
    """M11（Task 7 复核 Minor #2）：`引用标准名录` 块要有**解析级**回归用例，不能只有谓词断言。

    上一条用例只断言 `is_non_clause_title("引用标准名录")`，而 Task 7 真正修的是**解析行为**：
    该标题（无编号、无 `#` 之外的编号）此前不是候选行 → `### 引用标准名录` 这一行连同其下
    全部列表项被**并进上一条的 `content`**，成为用户可见的正文（且无法按非条文隐藏）。

    夹具按真实语料形状**逐字**构造（源：`data/outputs/95a76719/95a76719.md:587-620`，即 JGJ107；
    注意该块标题行与其上一行之间**没有空行**，这是原样保留的形状）。三条断言分别钉住：
    ① 标题自成一格且打标；② 它**不再**并进上一条（本用例的直接回归点）；③ 列表项随块保留
    （「打标保留」而非「直接过滤」，与目次的处置不同）。
    """
    md = (
        "#### 本规程用词说明\n"
        "\n"
        "2 条文中指明应按其他有关标准执行的写法为：“应符合……的规定”或“应按…执行”。\n"
        "### 引用标准名录\n"
        "\n"
        "1 《混凝土结构设计规范》GB 50010\n"
        "\n"
        "2《不锈钢棒》GB/T 1220\n"
        "\n"
        "10 《钢筋机械连接用套筒》JG/T 163\n"
    )
    got = {c["clause_no"]: c for c in parse_markdown(md)}
    assert "引用标准名录" in got and got["引用标准名录"]["is_non_clause"] is True
    prev = got["本规程用词说明"]
    assert "引用标准名录" not in prev["content"], "该块标题仍被并进上一条"
    assert "GB 50010" not in prev["content"], "该块的列表项仍被并进上一条"
    body = got["引用标准名录"]["content"]
    for item in ("《混凝土结构设计规范》GB 50010", "《不锈钢棒》GB/T 1220",
                 "《钢筋机械连接用套筒》JG/T 163"):
        assert item in body, f"列表项未随块保留（打标保留 ≠ 丢弃）：{item!r} 不在 {body!r}"

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


def test_vote_key_falls_back_after_toc_candidate_is_skipped():
    """M2（Task 3 复核 Minor #3）：`title_mode.get(..., False)` 的回退路径要有一条回归用例。

    为什么既有两条目次用例覆盖不到：它们都在目次之后跟一条 `## 1 总则`，把**预扫栈**与
    **主循环栈**重新对齐，于是后续条文的分组键在两个栈里相同 → 取得到值、走不到回退。

    分叉是这样产生的：`## 目次` 在预扫里**是**候选行（`_candidate_of` 认它）→ 被压入预扫栈
    → 其后 `1.0.1` 的分组键带上父键 `目次`；而主循环里目次行在 `is_filter_non_clause_title`
    处 `continue`（**在压栈之前**）→ 主循环栈为空 → `1.0.1` 的分组键父键是 `""` →
    **键缺失**。若这里改成 `title_mode[key]` 会直接 `KeyError` 崩掉整篇解析。

    夹具**刻意取标题形态的尾文本**（`1.0.1 正文甲`，短、无句末标点 → `_looks_like_title` 为真）：
    简报给的那条（`1.0.1 正文。`）只能证伪「键缺失 → 崩」，证伪不了**回退值的方向** —— 因为
    `is_titled = <回退值> and _looks_like_title(tail)` 的右半在 `正文。` 上恒为 False，
    回退写 True 也看不出来。换成 `正文甲` 后，回退若写 True 就会把它判成标题型 →
    该行无自身正文 → 被内节点判据丢弃（`title` 与 `content` 两条断言同时变红）。
    """
    md = "## 目次\n\n1.0.1 正文甲\n\n随后正文。\n"
    results = parse_markdown(md)
    assert [r["clause_no"] for r in results] == ["1.0.1"], \
        f"回退判成「带标题」会让该行按内节点被丢弃：{[r['clause_no'] for r in results]}"
    r = results[0]
    assert r["title"] == "", "回退值必须与平票规则同向（判「无标题」），文本须进 content"
    assert r["content"] == "正文甲\n随后正文。"
    assert r["is_non_clause"] is False


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


def test_candidate_of_rejects_zero_segment_on_non_hash_path():
    """M3（Task 3 复核 Minor #5 / Task 4 复核 Minor #4）：**无 `#` 路径**上的 0 段守卫要直接钉住。

    `_candidate_of` 有**两个** 0 段守卫：`#` 分支（走 `test_zero_segment_is_not_a_node` 的
    `### 3.0` 夹具）与 `m_num` 分支（`md_parser.py:356`）。后者此前**没有任何直接用例** ——
    删掉它本文件仍全绿，`3.0 节名`（真实文档里的节位占位行）会重新成为候选行、进而成节点。
    故本用例不经 `parse_markdown`、直接断言判据函数，并配一条**控制组**（`3.0.1` 必须照常
    是候选行），否则「恒返回 None」的实现也能让断言通过。
    """
    assert _candidate_of("3.0 不应存在的节") is None          # 节位占位行不成候选
    assert _candidate_of("3.0") is None
    assert _candidate_of("1.0 术语") is None
    # 控制组：0 段在**中间**时该行是条，不得被同一条守卫误伤（判据只看末段）
    assert _candidate_of("3.0.1 接头设计应满足强度要求。") == (3, "3.0.1", "接头设计应满足强度要求。")


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
    results = parse_markdown(md)
    nos = [c["clause_no"] for c in results]
    assert nos   # 夹具须产出条文，否则下面的断言是空断言
    assert "1" not in nos and "2" not in nos
    # ⚠ M4（Task 4 复核 Minor #6）：需求是「裸编号项**归属其所在的条**」，上式只断言了
    #    「不成条文」这**一半**——把裸编号行整行丢弃的实现同样能让它通过。补另一半：
    #    项自身的文本（`钢筋`/`水泥`）与行首编号都必须留在所属条 `content` 里（不丢内容）。
    #    实测 content == '正文甲。\n1 钢筋\n正文乙。\n2 水泥\n正文丙。'（行首编号原样保留）。
    #    另注：本夹具只产出 1 条，故 `results[0]` 恒为 `5.1.1`（上面 `nos` 断言已钉住）。
    assert "钢筋" in results[0]["content"] and "水泥" in results[0]["content"], \
        f"裸编号项的文本未归属其所在的条：{results[0]['content']!r}"
    assert "1 钢筋" in results[0]["content"], "项的行首编号被剥掉了（内容形态变了）"

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


def test_hash_heading_with_long_tail_is_still_title_type():
    """`#` 前缀行**无条件**判标题型：长尾（>20 字）也不得被 `_looks_like_title` 降级。

    回归守卫（final-fix Finding 1）：批一的「统一」把 `is_titled = 投票 and
    `_looks_like_title(tail)` 应用到**所有**候选行，而 `_looks_like_title` 拒收
    >20 字或句末标点结尾的 tail——旧实现的 `#` 分支从不调用它，`#` 标题恒为 title。
    长 `#` 标题（如 `### 21.4 防冲刷结构（锥坡、护坡、护岸、海墁、导流坝）` 22 字）
    被降级后：自身成为 title='' 的畸形条文，其子条文（21.4.1~4）的 parent_path 含
    空串、section_path 只剩编号、节名消失（实测 22 条 `#` 标题被降级）。

    可证伪：把主循环的 `is_titled` 恢复为 `title_mode.get(...) and
    `_looks_like_title(tail)`（去掉 `_is_hash_line(...) or` 前缀）后，下面这条
    `21.4` 的 title 变为 ''、`21.4.1` 的面包屑不再含「防冲刷结构」。
    """
    md = ("## 21 附属结构\n\n"
          "### 21.4 防冲刷结构（锥坡、护坡、护岸、海墁、导流坝）\n\n"
          "防冲刷结构应满足抗冲刷与防护功能要求。\n\n"
          "21.4.1 锥坡及护坡应符合下列规定。\n")
    clauses = {c["clause_no"]: c for c in parse_markdown(md)}
    # 长 `#` 标题自身必须是标题型（title 非空）
    assert clauses["21.4"]["title"] == "防冲刷结构（锥坡、护坡、护岸、海墁、导流坝）"
    # 其子条文的面包屑必须含该节名（而不是只剩 `21.4` 编号）
    assert "防冲刷结构" in clauses["21.4.1"]["section_path"], \
        f"长 `#` 标题的节名从子条文面包屑消失：{clauses['21.4.1']['section_path']!r}"


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


def test_pyright_config_includes_scripts():
    """M13 的**防倒退门禁**：`pyrightconfig.json` 的 `include` 必须含 `scripts`。

    守的是**那个洞**：`include` 只有 `["app","tests"]` 时 pyright **根本不看 `scripts/`**，
    于是「脚本 0 errors」全是空话 —— 实测（Task 12）往脚本注入类型错误后根 CLI 仍报
    `0 errors`；把 `scripts` 加进 `include` 后同一注入立刻报 `1 error`（定位到 270:41）。
    没有本用例的话，日后有人删掉这行，洞会**静默**重开（此前的「0 errors」结论又变成不可信）。

    放在本文件的原因：本文件已是批一各道门禁的落点（`survey_structure` 的覆盖率/重复号指标、
    `_NUM_PATTERNS` 循环形态的源码文本断言都在此），本用例同属「批一验收门禁」。

    ⚠️ 边界：只断言配置里**在**，不真跑 pyright —— 真跑要 node 侧 CLI（旁路工具链），
    不适合塞进 pytest。故它锁的是「配置没被悄悄删掉」，不是「pyright 此刻 0 errors」。
    """
    import json
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent.parent / "pyrightconfig.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "scripts" in cfg["include"], (
        f"scripts/ 不在 pyrightconfig 的 include 里（当前 {cfg['include']}）→ scripts/ 下的类型错误"
        f"不可见，「pyright 0 errors」对脚本是空话（M13，根因见本用例 docstring）"
    )


def test_survey_docstring_lists_every_metric(cjj2_md):
    """M15：脚本 docstring 的指标**数字/清单/返回键**三者必须一致（可失败的门禁）。

    背景：Task 8 的 docstring 写「产出五项指标」却列了六项（提交信息同），Task 11 再加 5 个键。
    人工维护的计数必然再次过期（本批 R-T10-8 的教训：一次「补样例」让两处计数同时过期），
    故改为机器比对：改 `_survey` 的返回键而不同步 docstring 即变红，反之亦然。
    """
    import re
    from scripts import survey_structure as ss
    listed = re.findall(r'^\s+(\d{1,2})\.\s+(\S+)', ss.__doc__ or "", re.M)
    assert listed, "docstring 的指标清单没被本门禁解析到（格式变了）"
    assert [int(n) for n, _ in listed] == list(range(1, len(listed) + 1)), "清单编号不连续"
    m = re.search(r'产出\s+\*{0,2}(\d+)\s*项', ss.__doc__ or "")
    assert m, "docstring 首行必须写明指标项数（阿拉伯数字，供本门禁读取）"
    keys = list(ss.survey_structure(cjj2_md))
    assert int(m.group(1)) == len(listed) == len(keys), (
        f"docstring 写 {m.group(1)} 项 / 清单列 {len(listed)} 项 / 返回 {len(keys)} 项，三者不一致"
    )
    assert [k for _, k in listed] == keys, "清单与实际返回键的顺序/名称不一致"


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
    # ⚠️ Task 14 随实现收敛而**收紧**这里（不是放宽）：`#` 分支的裸数字行收窄后，
    #    6 个伪 level-1 节点消失、真条文的面包屑归位 → 实测 0.9781 → 0.9986。
    #    故阈值由 0.95 提到 0.998（留 0.0006 余量，但不放走 Task 14 修掉的那 −0.0205）。
    assert stats["breadcrumb_coverage"] >= 0.998
    # ⚠️ 偏离简报（fix round 1，裁定）：原断言 `isinstance(..., list)` **空表也过**，
    #    是 M14 点名的「恒真门禁」。换成**集合断言**（实测恰为这六项）。
    #    它同时是 **R3 护栏**：脚本里「`X.0.Y` 的节位为 0 → 该级不存在」的剔除若被删掉，
    #    `1.0` / `2.0` / `3.0` / `23.0` 会立刻进集合（实测），本断言随即变红。
    #    另注：`missing_sections` 口径已收窄为**只统计条文行**，且**判断父级是否已存在
    #    也只用条文行**（R-T13-5 fix round 2）——若后者退回全量口径，本集合会缩回
    #    `['10.7','11.5','8']`（段内注释行 `14`/`17.5`/`18.8` 把三个真缺口掩盖掉），同样变红。
    # ⚠️ Task 14 随实现收敛而**收紧**这里（不是放宽）：原先那六项里 `14`/`17.5`/`18.8`
    #    的面包屑被伪 level-1 节点污染（实测 `14.3`→`'3'`、`17.5.8`→`'3'`、`18.8.9`→`'3'`），
    #    伪节点修好后 `18.8`/`8`/`11.5` 不再缺失（实测 `17.5.8` → `'17 斜拉桥 > 17.5 检验标准'`），
    #    缺口集合由 6 项收敛为 3 项。
    # ⚠️ 措辞订正（批一验收 Task 12，同 M16 的「文档层面防误读」类）：本行原写
    #    「**遗留的这 3 项是真实缺口**」—— 那是 Task 14 当时的判断，**已被 Task 11 的
    #    反事实实测推翻**：3 项的成因是**同一类 PDF 断行伪影**（其行首被截断成编号形态
    #    → `10.7.3`/`17.5.1`/`14.3` 成了错位节点），把这三行的前缀补回「本规范第」使其
    #    不再是候选行后 `missing_sections` 立刻为 `[]`。即**真正的规范缺口为 0**，
    #    这 3 项只是伪影的表现（详见 TODOS.md T22 与裁定 R-T14-4）。
    # ✅ **随 T22 修复而更新（fix/parser-three-narrowings 的 fix ③，可预期、非回归）**：
    #    三条伪影行（L3077/L4802/L4926）不再成节点 → `missing_sections` 归零，
    #    与 T22 记录的反事实实测（`[]`）一致。缺口集合由 3 项收敛为 0 项。
    assert stats["missing_sections"] == []


def test_survey_reports_duplicates(cjj2_md):
    """重复条文号必须被量化，且**数字要可证伪**（初稿只断言 `isinstance(dict)`，是摆设）。

    期望值 = Task 13+14 终态的实测（控制器探针，本 Task 在 HEAD 6ad461d 复现）：
      重复组 123 / 重复行 258 / 其中 is_non_clause=1 的 135
    任一项不符即说明重复形态变了 —— 必须解释后再改此断言，不得静默更新。

    **成因分解（本 Task 的交付物，逐个由探针实测得出，123 组无余项）**：

    | 成因 | 组数 | 形态 |
    |---|---|---|
    | ① 设计性：正文 + 条文说明同号 | **119** | 1 条正文 + N 条逐款解释；注释侧已被 Task 13 打标 |
    | ② 真重复：两次都在正文 | **2** | `10.7.3`、`17.5.1` —— PDF 断行把交叉引用劈成两行 |
    | ③ 条文说明段内自重复 | **2** | `前言`、`2`（段内两个同名子标题/块） |

    ⚠️ ①②③ 的组数**只能**由 `duplicate_group_kinds`（组级）守住；`duplicate_rows` /
    `duplicate_rows_is_non` 是弱信号，会在 ①↔③ 互换时保持不变（本用例末尾有实测记录）。

    ① 的样例：`16.8.3` 共 6 行 = 1 条正文（夹具 L4177）+ 5 条注释（L7120/7122/7124/7126/7128，
    逐条解释第 2/3/4/6/7 款）。① 是**合法形态**，不是缺陷。

    ② 的两个源行（夹具，逐行实测）：
      `L3077: '10.7.3 条第2款的规定。'`  ← 上文 L3076 `1 …应符合本规范第`
      `L4802: '17.5.1 条和第 13.7.2 条规定。'` ← 上文 L4801 `17.5.4 …应符合本规范第`
    两行都以「条文号形状的 token」起头（PDF 把交叉引用 `…第10.7.3条第2款…` 从中间断行），
    于是被 `_candidate_of` 认成候选行 → 各自与正文侧同号行构成重复。
    ② 本批**不修**（R-T14-3：解析器改动封板），已记 TODOS.md（T22，含源行号）。

    ⚠️ **② 的正确描述是「错位节点 + 吞并正文」，且字符量必须标口径**：② 的两条伪影节点
    合计 **raw 3134 字符 / plain 484 字符**（`10.7.3` 那条 = raw 3119 / plain 469；
    `17.5.1` 那条 = 15 / 15）。raw 与 plain 相差 **6.5 倍**，差额全部是那条伪节点**吞掉的
    `表 11.5.6-1` 渲染载荷**（HTML/LaTeX 标记，`plain_text` 下被剥离）——它的面包屑是
    `11 墩台 > 11.5 检验标准`，即**本属 11.5.6 的表格正文被记到了第 11 章下**。
    故 ② 不只是「号码重复」，还是**数据污染**；只写「2 处真重复、484 字符」（plain 口径）
    会漏掉 raw 侧的 3119。全篇基准同口径：raw 445906 / plain 145269。

    同一伪影类在第 3 行（`L4926: '14.3 节有关规定，且应符合下列规定：'`，上文 L4924
    `17.5.8 …应符合本规范第`）上**不产生重复号**，而是落成 `clause_no='14.3'` 的错位节点
    （面包屑 `17 斜拉桥`，吞掉其后表 17.5.8-1 正文），**这正是 `missing_sections` 那 3 项
    （`10.7`/`14`/`17.5`）的唯一成因**——反事实实测：把这三行前缀补上「本规范第」使其
    不再是候选行，`missing_sections` 立刻变为 `[]`（重复组同时 123→121、重复行 258→254）。
    故「3 个缺口」与「2 个真重复」是同一伪影类的两种表现，必须一并读。
    """
    from scripts.survey_structure import survey_structure
    stats = survey_structure(cjj2_md)
    dups = stats["duplicate_clause_no"]
    assert isinstance(dups, dict)
    assert all(v > 1 for v in dups.values()), "重复组里混进了单次出现的号"
    assert len(dups) == 121, f"重复组数 {len(dups)}（预期 121）"
    assert stats["duplicate_rows"] == 254, f"重复行数 {stats['duplicate_rows']}（预期 254）"
    assert stats["duplicate_rows_is_non"] == 135, \
        f"重复行中打标的 {stats['duplicate_rows_is_non']}（预期 135）——" \
        f"条文说明段的同号重复应全部打标（Task 13）"
    # ⚠️ **组级**断言（fix round 1）：上面三个数字是**弱信号**——`duplicate_rows_is_non`
    #    把 `designed`（合法）与 `commentary_only`（段内自重复）混在同一个数字里，
    #    ① ↔ ③ 此消彼长时它**纹丝不动**（实测：两处 flag 翻转后 123/258/135 **全部不变**，
    #    而下面的三元组由 119/2/2 变 121/1/1）。故必须精确等值断言**三元组**。
    # ✅ **随 T22 修复而更新（fix ③，可预期、非回归）**：三条伪影行不再成节点 →
    #    `body_only` 2→0、重复组 123→121、重复行 258→254（T22 记录的反事实实测吻合）。
    assert stats["duplicate_group_kinds"] == \
        {"designed": 119, "body_only": 0, "commentary_only": 2}, \
        f"重复号的组级分解 {stats['duplicate_group_kinds']}（预期 119/0/2）——" \
        f"designed=正文+条文说明同号（合法）、body_only=真重复、commentary_only=段内自重复"
    # ⚠️ **号身份**断言（fix round 2）：上面三元组仍是**计数**——「`body_only` 有 N 组」并
    #    不等于「就是哪几个号」。本 Task 要写进验收报告的结论恰是**号身份**（真重复是
    #    `10.7.3`/`17.5.1`，由夹具 L3077/L4802 造成），**它必须被守住**，否则某个 `body_only`
    #    号被另一个新伪影号**替换**（计数不变）时，报告会**无声地继续宣称旧结论**。
    #    依本批范式（Task 10 冻结的是**具体字典** `{'6.1.1':'模板',…}` 而非「3 条标签」）：
    #    **冻具体值，不冻计数**。只钉两个短集合；`designed` 有 119 个号，计数已够，不逐号断言。
    #    ✅ **已随 T22 修复更新为 `set()`（可预期、非回归）**：伪影消失后 `body_only` 为空集，
    #    与 TODOS T22 尾注约定的「伪影消失时即 `set()`」一致。
    members = stats["duplicate_group_kinds_members"]
    assert set(members["body_only"]) == set(), \
        f"真重复的号身份 {members['body_only']}（预期空集——伪影已修掉）——" \
        f"修复前为 10.7.3 / 17.5.1（夹具 L3077/L4802）"
    assert set(members["commentary_only"]) == {"前言", "2"}, \
        f"条文说明段内自重复的号身份 {members['commentary_only']}（预期 前言 / 2）"


# ═══════════════════════════════════════════
# 组 5b：`#` + 裸数字行的候选收窄（Task 14）
# ═══════════════════════════════════════════

def test_hash_line_with_bare_number_and_prose_is_not_a_node():
    """`#` + 裸数字 + 正文句 → 不是节点（OCR 会给表格/款文本误加 `##`）。

    实测（夹具行 4901）：`## 3 钢箱梁悬臂拼装允许偏差应符合表17.5.7-2的规定。`
    成了 clause_no='3'、level 1 的候选 → 弹空栈、吞掉其后表格正文，
    并把真条文 18.8.10 的面包屑污染成该伪节点标题。
    """
    md = ("## 11 墩台\n\n### 11.5 检验标准\n\n"
          "11.5.3 现浇混凝土墩台允许偏差应符合下列规定。\n\n"
          "## 4 现浇混凝土柱允许偏差应符合表11.5.3-2的规定。\n\n"
          "表 11.5.3-2 现浇混凝土柱允许偏差\n")
    rows = parse_markdown(md)
    assert not any(r["clause_no"] == "4" for r in rows), "伪 level-1 节点仍在"
    assert not any(r["section_path"].startswith("4 ") for r in rows), "面包屑被伪节点污染"
    # 该行的文本必须**归属其所在的条**——**当存在可归属的候选行时**（不是丢掉，也不是自成一条）。
    # ⚠️ 前提已按复核 F1 收窄：若该行**是首个候选行**（或其后只有内节点章标题），其自身文本**连同
    #    其后的正文**会无处归属而被丢弃。**丢多少取决于构造**（= 该行自身文本 + 其后直到下一个候选
    #    行之前的全部正文），故此处**不写死魔数** —— 可自行复现的构造：
    #      `## 4 现浇混凝土柱允许偏差应符合表 4.0.1 的规定。` 作首个候选行 → 修复前 1 条 /
    #      修复后 **0 条**（实测丢弃：仅该行 25 字符；其后再接一行表格题
    #      `表 4.0.1 现浇混凝土柱允许偏差` 时为 44 字符）。
    #    复核者各自构造下的观测值（**非本判据的常量**）为 41 与 46 字符。
    #    📌 同类值一律按本批标准处理：**引用测量值必须给出可复现的构造**（本 Task 的 F3 就是
    #    「一句不可复现的『实测』值」被复核抓出来的）。
    #    该行为**非 `#` 路径既有**（对照用例修复前后完全一致：实测两边都是 0 条 / 0 字符），
    #    本 Task 只让 `#` 分支与之对齐，故为**已登记的已知增量**，不是新一类丢失；CJJ2 上不触发
    #    （净 +13）；真实 OCR 输出带 `## 第X页` 页标记（保留为候选）故栈通常非空。
    #    已记 TODOS.md（T20：缓冲孤儿文本挂到下一候选行，属**行为设计变更**，本批不做）。
    target = [r for r in rows if r["clause_no"] == "11.5.3"]
    assert target and "现浇混凝土柱允许偏差" in target[0]["content"], \
        "被收窄的行其文本应归入所属条（当存在可归属的候选行时）"


def test_hash_line_with_bare_number_and_chapter_name_is_still_a_node():
    """短章名照旧是节点 —— 本 Task 的安全边界（收窄不得误伤真章标题）。"""
    md = ("## 3 施工准备\n\n### 3.1 一般规定\n\n3.1.1 施工准备应符合下列规定。\n"
          "1 施工单位应编制施工组织设计。\n")
    rows = parse_markdown(md)
    hit = [r for r in rows if r["clause_no"] == "3.1.1"]
    assert hit and hit[0]["section_path"] == "3 施工准备 > 3.1 一般规定"


def test_long_chapter_name_with_bare_number_is_still_a_node():
    """长章名（>20 字）不得被 20 字长度门误伤 —— 本 Task 判据取舍的**唯一**可证伪护栏。

    ⚠️ 该章名是**假设性构造**：夹具只有其短版 `3 施工准备`，全仓语料无此长版（已 grep 核实）。
    它存在的理由：实测选定判据（只取句末标点）与被否决判据（`not _looks_like_title(...)`）
    在 CJJ2 上**数值完全相同**（都是 −6 行、覆盖率 0.9986）→ 语料**无法**区分二者，
    只有本用例能。变异 M3（把判据换成被否决的那条）下本用例**必须变红**（已实跑验证）：
    没有它，未来有人把判据换成 `_looks_like_title`，语料上毫无差别，而长章名被静默降级、
    其下条文的章节关系一起丢失，无人能发现。
    """
    md = ("## 3 施工准备与临时设施（含施工便道、临时用电）\n\n"
          "### 3.1 一般规定\n\n3.1.1 施工准备应符合下列规定。\n")
    rows = parse_markdown(md)
    hit = [r for r in rows if r["clause_no"] == "3.1.1"]
    assert hit, "长章名被误判后，其下条文一同丢失"
    # ⚠️ final-fix（Finding 1）后 `#` 行恢复标题型语义：`3` 章名是**内节点**、不再
    #    自成一格 body 条文（`any(clause_no == "3")` 随之不成立，与组 3 的
    #    `test_inner_node_without_body_is_not_emitted_but_serves_as_ancestor` 一致）。
    #    但它的**标题文本必须保留在子条文面包屑里** —— 这正是本护栏的语义意图，
    #    且比旧断言更强：若未来有人把 `_candidate_of` 的判据换成 `_looks_like_title`
    #    （M3），`## 3 <长章名>` 会被整体筛掉，面包屑不含「施工准备与临时设施」→ 本断言变红。
    assert hit[0]["section_path"].startswith("3 施工准备与临时设施"), \
        f"长章名未进入面包屑：{hit[0]['section_path']!r}"


def test_cjj2_has_no_bare_number_nodes_carrying_prose(cjj2_md):
    """端到端：CJJ2 里裸数字节点的内容量不得再是大块正文。

    实测 5,176 → 1,712 字符、22 → 16 条。余下 16 条**并非全是章节点**：13 条是真章标题，
    另 3 条在**条文说明段内部**，是注释子标题而非章 —— 夹具行号逐个列出：
    `1 一次张拉法`（L7214）、`2 多次张拉`（L7218）、`2 中塔柱施工防倾措施`（L7171）。
    ⚠️ fix round 4 更正：原写区间「夹具 L7144–L7218」的**起点指错了对象**（L7144 是另一个节点
    `## 17 斜拉桥`，实测），故改为逐个列出行号。计数 `== 16` 不变，仅描述订正
    （fix round 3 / R-T14-8：原写「余下 16 条都是章节点」说过头了）。
    """
    from app.ai.text_clean import plain_text
    bare = [c for c in parse_markdown(cjj2_md) if c["clause_no"].isdigit()]
    assert len(bare) == 16, f"裸数字节点数 {len(bare)}（预期 16）"
    total = sum(len(plain_text(c["content"])) for c in bare)
    assert total < 2_500, f"裸数字节点内容量 {total}——伪节点仍在吞正文"


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


# ═══════════════════════════════════════════
# 组 7：三处收窄（fix/parser-three-narrowings：① 章号单调 / ③ 断行重复 / ④ 目录点引行）
# ═══════════════════════════════════════════

def test_hash_bare_digit_chapter_number_must_strictly_increase():
    """fix ①：body 内 `#`+裸数字节点的章号必须**严格递增**，重复/回跳的章号不是节点。

    实测（JGJ107 `data/outputs/aa96b73a/aa96b73a.md:422`）：附录 A 里
    `## 2 变形测量标距` 是 A.1.1 的**子项**、被 OCR 误加 `##`，成了 chapter 2 的
    重复节点（真章号 1..7，`2` 在 `7` 之后再次出现）。被拒行的文本像普通非候选行
    一样折入所属条（A.1.1）的 content。
    """
    md = (
        "## 1 总则\n\n1.0.1 正文甲。\n\n"
        "## 2 术语和符号\n\n2.0.1 正文乙。\n\n"
        "### A.1 型式检验\n\n"
        "A.1.1 试件型式检验的仪表布置和变形测量标距应符合下列规定：\n\n"
        "1 单向拉伸试验时的仪表应布置在钢筋两侧。\n\n"
        "## 2 变形测量标距\n\n"
        "1）单向拉伸残余变形测量应按下式计算。\n"
    )
    rows = parse_markdown(md)
    assert not any(r["clause_no"] == "2" for r in rows), "重复的裸章号 2 仍是节点"
    a11 = [r for r in rows if r["clause_no"] == "A.1.1"]
    assert a11, "A.1.1 应产出"
    assert "变形测量标距" in a11[0]["content"], "被拒行的文本应折入所属条 content"


def test_hash_bare_digit_chapter_number_must_not_decrease():
    """fix ① 唯一可证伪的回跳形态：`## 3` 之后出现**从未见过的**裸 `## 2`。

    现有用例 test_hash_bare_digit_chapter_number_must_strictly_increase 用的是「重复的 2」
    （`## 2 术语和符号` 后再 `## 2 变形测量标距`），而 `2` 已进 seen_clause_nos → 即便
    revert 掉 fix ①，fix ③ 的同号重复判据仍会拒掉它 → 该用例照常绿（round 2 Finding 2）。
    本用例的 `2` **从未作为条号出现**（章号直接 1→3），fix ③ 拦不住（`2` 不在 seen），
    只有 fix ① 的 `n <= max_bare_chapter` 能拒。revert fix ①（保留 ③）时本用例必红。
    """
    md = (
        "## 1 总则\n\n1.0.1 正文甲。\n\n"
        "## 3 施工准备\n\n3.0.1 正文丙。\n\n"
        "### A.1 型式检验\n\n"
        "A.1.1 试件型式检验应符合下列规定：\n\n"
        "1 单向拉伸试验。\n\n"
        "## 2 变形测量标距\n\n"
        "1）单向拉伸残余变形测量应按下式计算。\n"
    )
    rows = parse_markdown(md)
    assert not any(r["clause_no"] == "2" for r in rows), "回跳的裸章号 2 仍是节点（fix ① 失效）"
    a11 = [r for r in rows if r["clause_no"] == "A.1.1"]
    assert a11, "A.1.1 应产出"
    assert "变形测量标距" in a11[0]["content"], "被拒行的文本应折入所属条 content"


def test_cross_reference_fragment_is_not_a_node():
    """fix ③ (T22)：PDF 断行把交叉引用劈开，下半行以条号形状 token 起头，
    被误认成候选行。判据：body 内同号**已出现过** → 不是节点，文本折入当前条。

    夹具 `tests/fixtures/cjj2_source.md` 的两处形态：
    ① L4926 `14.3 节有关规定，且应符合下列规定：` —— 正文已有节标题 `### 14.3 检验标准`，
       故 `14.3` 是同号重复（曾吞掉其后 `表 17.5.8-1` 4504 字符）；
    ② L4802 `17.5.1 条和第 13.7.2 条规定。` —— 正文真 `17.5.1` 之后的分片重复。
    """
    md = (
        "## 14 钢梁\n\n### 14.3 检验标准\n\n"
        "14.3.1 钢梁质量检验应符合下列规定。\n\n"
        "## 17 斜拉桥\n\n### 17.5 检验标准\n\n"
        "17.5.8 结合梁的工字钢梁段悬臂拼装质量检验应符合本规范第\n\n"
        "14.3 节有关规定，且应符合下列规定：\n\n"
        "表 17.5.8-1 结合梁的工字钢梁段悬臂拼装允许偏差\n"
    )
    rows = parse_markdown(md)
    assert not any(r["clause_no"] == "14.3" for r in rows), "跨章伪节点仍在"
    c = [r for r in rows if r["clause_no"] == "17.5.8"]
    assert c and "14.3 节有关规定" in c[0]["content"], "被拒行的文本应折入当前条"

    md2 = (
        "## 17 斜拉桥\n\n### 17.5 检验标准\n\n"
        "17.5.1 悬臂浇筑混凝土主梁质量检验应符合下列规定。\n\n"
        "17.5.4 支架上浇筑混凝土主梁质量检验应符合本规范第\n"
        "17.5.1 条和第 13.7.2 条规定。\n"
    )
    rows2 = parse_markdown(md2)
    assert sum(1 for r in rows2 if r["clause_no"] == "17.5.1") == 1, "同号重复仍在"
    c2 = [r for r in rows2 if r["clause_no"] == "17.5.4"]
    assert c2 and "17.5.1 条和第 13.7.2 条规定" in c2[0]["content"], "被拒行文本应折入当前条"


def test_toc_dot_leader_lines_kept_out_of_content():
    """fix ④：目录点引行（≥5 连续点）从不是规范正文，不得进 pending/content。

    夹具 `tests/fixtures/cjj2_source.md` 无「目次」标题（OCR 丢了它），故
    `discard_section` 不触发，138 行点引行（L94 起，如 `1 总则 ..... 1`）曾漏进
    `前言` 的 content。`_match_clause_line` 已据此排除候选行，此处补「不进 pending」
    这后一半。
    """
    md = (
        "## 前言\n\n"
        "本规范为适应混凝土结构工程发展的需要而编制。\n\n"
        "1 总则 ..... 1\n"
        "2 术语 ..... 3\n\n"
        "## 1 总则\n\n"
        "1.0.1 正文内容。\n"
    )
    rows = parse_markdown(md)
    qy = [r for r in rows if r["title"] == "前言"]
    assert qy, "前言应产出"
    assert "总则 ..... 1" not in qy[0]["content"], "目录点引行仍留在前言 content"
    assert "术语 ..... 3" not in qy[0]["content"], "目录点引行仍留在前言 content"
    assert "为适应混凝土结构工程发展的需要而编制" in qy[0]["content"], "前言正文不得被误删"


def test_numeric_filter_title_seen_advances_identically():
    """fix round 2（Finding 3）：数字命名的过滤标题不得让预扫与主循环的 seen 集合分叉。

    `## 1.1 目次` 是**数字命名的过滤标题**：`_vote_title_mode` 把它当普通候选行、推进
    `seen_clause_nos`（加 "1.1"）；主循环在 `is_filter_non_clause_title` 处 continue
    （在 `seen_clause_nos.add` 之前）→ 主循环的 seen 没有 "1.1"。于是其后 `## 1.1 总则`
    在预扫被 `_reject_cross_chapter` 判「同号重复」而拒、主循环却照常接受并产出条文——
    两个 pass 的候选行集合分叉，违背「两遍用同一判据」的不变量（两语料的过滤标题都是
    非数字，故该洞是潜伏的、语料上不触发）。
    修法：两遍都在 `_reject_cross_chapter` 之后、任何后续分支之前推进 seen。
    修后 `## 1.1 总则` 在两边都被判重复 → 不再产出条文；其子条 1.1.1 的面包屑不再含 `1.1 总则`。
    """
    md = (
        "## 1.1 目次\n\n"
        "1.1 总则 ..... 1\n\n"
        "## 1.1 总则\n\n"
        "1.1.1 正文甲。\n"
    )
    rows = parse_markdown(md)
    assert not any(r["clause_no"] == "1.1" for r in rows), \
        f"数字目次标题后主循环仍产出 `## 1.1 总则`（seen 分叉）：{[r['clause_no'] for r in rows]}"
    hit = [r for r in rows if r["clause_no"] == "1.1.1"]
    assert hit, "1.1.1 应产出"
    assert hit[0]["section_path"] == "", \
        f"分叉后 1.1.1 的面包屑错误带上 `1.1 总则`：{hit[0]['section_path']!r}"


# ═══════════════════════════════════════════
# 组 8：次分组标签空壳节点打标隐藏（feature ②，round 2）
# ═══════════════════════════════════════════

def test_subgroup_label_only_body_is_non_clause():
    """feature ②：自身正文**完全**由次分组标签组成的节点 → is_non_clause=True（保留但隐藏）。

    CJJ2 的 `### 9.6 检验标准` 后紧跟 `#### 主控项目`（标签行非候选 → 折入 9.6 自身正文），
    使 9.6 成为 content 仅「主控项目」4 字的空壳条文。这类节点应打标隐藏；其子条照常解析、
    面包屑仍含该节名（stack 内节点的 is_non_clause 不变，故 `_build_section_path` 不丢它）。
    """
    md = ("### 5 模板\n\n### 5.4 检验标准\n\n#### 主控项目\n\n"
          "5.4.1 模板制作应符合下列规定。\n\n检查数量：全数检查。\n")
    rows = {c["clause_no"]: c for c in parse_markdown(md)}
    assert rows["5.4"]["is_non_clause"] is True, "label-only 空壳节点未打标"
    assert rows["5.4"]["content"] == "主控项目", "打标不改变其自身正文（保留）"
    assert rows["5.4.1"]["is_non_clause"] is False, "子条不得被误打标"
    assert rows["5.4.1"]["section_path"] == "5 模板 > 5.4 检验标准", "子条面包屑不得丢节名"


def test_subgroup_label_only_body_with_roman_prefix_and_control():
    """feature ②的罗马数字前缀形态 + 控制组（标签夹在正文中间不得打标）。

    罗马前缀来自 R7（182 号第三十三条）：次分组单元用大写罗马数字编号（`Ⅰ 主控项目`）。
    控制组（14.3.1 形态）：`主控项目`/`一般项目` 夹在正文中间是有效的分组标记，不得打标。
    """
    md = "### 9 砌体\n\n### 9.6 检验标准\n\n#### Ⅰ 主控项目\n\n9.6.1 石材强度应符合设计要求。\n"
    rows = {c["clause_no"]: c for c in parse_markdown(md)}
    assert rows["9.6"]["is_non_clause"] is True, "带罗马前缀的 label-only 空壳未打标"

    md2 = ("### 14 钢梁\n\n### 14.3 检验标准\n\n"
           "14.3.1 钢梁质量检验应符合下列规定：\n\n#### 主控项目\n\n"
           "1 钢材品种应符合设计要求。\n\n检查数量：全数检查。\n\n#### 一般项目\n\n"
           "6 焊缝外观应符合规定。\n\n"
           "14.3.2 钢梁现场安装检验应符合下列规定：\n\n正文乙。\n")
    rows2 = {c["clause_no"]: c for c in parse_markdown(md2)}
    assert rows2["14.3.1"]["is_non_clause"] is False, "标签夹在正文中间的分组标记不得导致打标"
    assert "主控项目" in rows2["14.3.1"]["content"]
    assert "一般项目" in rows2["14.3.1"]["content"]


def test_cjj2_label_only_sections_are_non_clause(cjj2_md):
    """feature ②端到端：CJJ2 恰好 6 个「自身正文全是次分组标签」的空壳节被打标（168 → 174）。

    6 行均为 `### X 检验标准` 后紧跟一个 `主控项目` 标签行（无其他正文），其 content 恰为
    「主控项目」4 字。JGJ107 语料 0 行（无任何 `主控项目`/`一般项目`，由探针实测、不进本用例）。
    """
    clauses = parse_markdown(cjj2_md)
    expect = {"5.4", "6.5", "7.13", "8.5", "9.6", "12.5"}
    label_only = [c for c in clauses if c["clause_no"] in expect]
    assert {c["clause_no"] for c in label_only} == expect, \
        f"label-only 空壳节集合变了：{[c['clause_no'] for c in label_only]}"
    assert all(c["is_non_clause"] and c["content"] == "主控项目" for c in label_only), \
        "6 个空壳节应全部打标且 content 仍为「主控项目」"
    marked = sum(1 for c in clauses if c["is_non_clause"])
    assert marked == 174, f"打标总数 {marked}（预期 174 = 168 + 6）"
