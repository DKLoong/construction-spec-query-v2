"""jieba 预分词工具（app/search/tokenize.py）单元测试

验证：中文词组分词、纯标点过滤、search_text 构建（含 clause_no 原文追加）、
FTS MATCH 查询串构造、确定性（一致性基础）、面包屑的词内空白归一化（U16）。
"""

from app.search.tokenize import (
    tokenize, build_search_text, build_match_query, normalize_cjk_spacing,
)


def test_tokenize_chinese_words():
    """中文按词组分词（非逐字），保证 BM25 对中文有效"""
    assert tokenize("钢筋混凝土") == ["钢筋", "混凝土"]
    assert tokenize("套筒") == ["套筒"]
    assert tokenize("钢筋机械连接") == ["钢筋", "机械", "连接"]


def test_tokenize_filters_punctuation():
    """纯标点 token 被过滤（jieba 会把 "5.1.1" 拆出 "."）"""
    toks = tokenize("5.1.1")
    assert "." not in toks, "点号不应作为 token 进入 FTS 查询"
    assert all(t != "." for t in toks)


def test_tokenize_empty():
    """空/空白输入返回空列表"""
    assert tokenize("") == []
    assert tokenize("   ") == []


def test_build_search_text_returns_two_columns():
    st, bc = build_search_text("6.3.1", "接头安装", "应满足强度要求",
                               "6 接头的现场加工与安装 > 6.3 接头安装")
    assert isinstance(st, str) and isinstance(bc, str)
    assert "接头" in bc                      # 面包屑进了面包屑列
    assert "接头的现场加工" not in st          # 关键：search_text 不含面包屑


def test_search_text_excludes_breadcrumb():
    """面包屑**只**通过 breadcrumb 列参与，不得同时拼进 search_text。

    否则它会以权重 1.0（埋在正文里）与 breadcrumb_weight 各计一次，
    权重语义失真（这是 spec §4.3 明确的设计边界）。
    """
    st, _bc = build_search_text("1.0.1", "总则", "正文甲", "9 某章 > 9.9 某节")
    assert "某章" not in st and "某节" not in st


def test_breadcrumb_empty_when_no_section_path():
    """无祖先时 breadcrumb 为空串（不是 None，触发器用 COALESCE 兜底）"""
    _st, bc = build_search_text("1.0.1", "", "正文甲")
    assert bc == ""


def test_build_search_text_combines_title_content():
    """search_text = jieba(标题+正文) 空格连接"""
    st, _bc = build_search_text("", "模板设计", "模板及其支架应根据工程结构形式进行设计。")
    assert "模板" in st
    assert "设计" in st
    assert "支架" in st
    # 分词用空格连接
    assert st == " ".join(st.split())


def test_build_search_text_appends_clause_no_raw():
    """clause_no 原文追加（不走 jieba，保留编号检索能力）"""
    st, _bc = build_search_text("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。")
    assert st.endswith("5.1.1"), f"clause_no 应原样追加到 search_text 末尾，实际: {st!r}"


def test_build_search_text_empty_returns_space():
    """空文本：search_text 返回占位空格（避免 FTS5 索引 NULL 报错）；breadcrumb 为空串"""
    st, bc = build_search_text("", "", "")
    assert st == " "      # 旧断言为 build_search_text("","","") == " " —— 语义未变，只是解包
    assert bc == ""


def test_build_search_text_deterministic():
    """同输入同输出（分词一致性：索引/查询两侧可稳定对齐）"""
    a, a_bc = build_search_text("2.1.4", "套筒", "套筒 coupler or sleeve 是钢筋机械连接的关键部件。",
                                "2 术语 > 2.1 术语和符号")
    b, b_bc = build_search_text("2.1.4", "套筒", "套筒 coupler or sleeve 是钢筋机械连接的关键部件。",
                                "2 术语 > 2.1 术语和符号")
    assert a == b
    assert a_bc == b_bc


def test_build_search_text_strips_markup_residue():
    """含 PaddleOCR-VL 标记的条文：索引不得出现标记 token，正文与单元格文字保留"""
    content = (
        "I 级、Ⅱ级、Ⅲ级接头的极限抗拉强度必须符合表3.0.5的规定。\n"
        '<div style="text-align: center;">表3.0.5 接头极限抗拉强度</div>\n'
        "<table border=1 style='margin: auto;'>"
        "<tr><td style='text-align: center;'>接头等级</td>"
        "<td colspan=\"2\">连接件型式</td></tr></table>"
        "<img src='imgs/a.jpg' alt=\"Image\" />"
        "抗拉强度 $ N/mm^{{2}} $ 应符合要求"
    )
    st, _bc = build_search_text("3.0.5", "", content)
    toks = {w.lower() for w in st.split()}
    leftover = toks & {
        "td", "tr", "style", "table", "div", "center", "img", "src", "alt",
        "image", "colspan", "rowspan", "border", "margin", "auto", "text",
        "align", "wrap", "break", "word", "mathrm", "times", "N/mm",
    }
    assert not leftover, f"索引残留标记 token: {sorted(leftover)}"
    # 正文与单元格文字仍可检索（断言 token 而非整词：jieba 会再切分）
    assert {"抗拉强度", "接头", "等级", "连接件", "型式", "符合"} <= toks
    assert st.endswith("3.0.5")


def test_build_search_text_plain_content_unchanged():
    """无标记条文的索引结果不得改变（依赖 plain_text 幂等，否则全库索引口径漂移）"""
    content = "套筒 coupler 是钢筋机械连接的关键部件。"
    st, _bc = build_search_text("", "", content)
    assert st == " ".join(tokenize(content))


def test_build_match_query_quotes_tokens_with_and():
    """MATCH 查询串：token 加引号、AND 连接（jieba 会切分标点，故 token 不含引号）"""
    q = build_match_query("钢筋 混凝土")
    assert q == '"钢筋" AND "混凝土"'
    # 每个 token 都被双引号包裹（防 FTS5 语法注入裸词）
    for tok in tokenize("钢筋 混凝土"):
        assert f'"{tok}"' in q


def test_build_match_query_empty():
    """切不出有效词时返回空串（调用方走纯 SQL 分支）"""
    assert build_match_query("") == ""
    assert build_match_query("。。。") == ""


def test_build_match_query_or_joins_tokens():
    """OR 变体：token 用 OR 连接（AND 空结果时的降级召回用）"""
    q = build_match_query("I级接头强度", "OR")
    assert q == '"I" OR "级" OR "接头" OR "强度"'
    # 默认仍是 AND（不破坏原调用）
    assert build_match_query("I级接头强度") == '"I" AND "级" AND "接头" AND "强度"'


# ═══════════════════════════════════════════
# U16：面包屑的词内空白归一化（派生文本，不动 section_path）
# ═══════════════════════════════════════════

#: 分词器会把 `1 总 则` 切成 `1 / 总 / 则`（词的**字面被空格劈开**），
#: 于是查「总则」永远命中不了这一节。归一化只折叠**两侧都是 CJK 表意文字**的空白。
_SPACED_SECTIONS = [
    ("1 总 则", "1 总则", "总则"),
    ("6 钢 筋", "6 钢筋", "钢筋"),
    ("12 支 座", "12 支座", "支座"),
    # 多级路径 + 节号点段：每级各自折叠，编号/分隔符不动
    ("10 基 础 > 10.4 沉 井", "10 基础 > 10.4 沉井", "沉井"),
]


def test_normalize_cjk_spacing_collapses_intra_word_spaces():
    """「CJK 空白 CJK」折叠；一个或多个空白（含全角空格 U+3000）都折叠"""
    for spaced, collapsed, _word in _SPACED_SECTIONS:
        assert normalize_cjk_spacing(spaced) == collapsed, f"{spaced!r} 未折叠"
    # 多个空白一起折叠
    assert normalize_cjk_spacing("总  则") == "总则"
    assert normalize_cjk_spacing("总　　则") == "总则"
    # 全角空格（U+3000）同样是「词内空白」
    assert normalize_cjk_spacing("钢　筋") == "钢筋"
    assert normalize_cjk_spacing("1　总　则") == "1　总则", \
        "数字与 CJK 之间的空白不属词内空白（保留），只折叠 CJK–CJK 的那一处"
    # 空串/无空白：原样返回（幂等）
    assert normalize_cjk_spacing("") == ""
    assert normalize_cjk_spacing("1 总则") == "1 总则"


def test_normalize_cjk_spacing_is_idempotent():
    """幂等：归一化后再归一化不变（否则面包屑与查询侧会漂移）"""
    for spaced, collapsed, _word in _SPACED_SECTIONS:
        assert normalize_cjk_spacing(normalize_cjk_spacing(spaced)) == collapsed


#: 非词内空白形态 → 归一化必须**逐字不动**，且面包屑 token 流与归一化前逐 token 相同。
#: 每组是（section_path, 期望 token 列表）。
_UNTOUCHED_SPACING = [
    # 路径分隔符两旁的空白（`>` 非 CJK）
    ("6 混凝土分项工程 > 6.1 接头安装", ["6", "混凝土", "分项", "工程", "6.1", "接头", "安装"]),
    # 纯 ASCII/数字段
    ("GB 50010", ["GB", "50010"]),
    ("GB/T 1499.1-2017 > GB 50010", ["GB", "T", "1499.1", "2017", "GB", "50010"]),
    # 数字后接 CJK（`6.3.1 接头`）：单个空白保留
    ("6.3.1 接头", ["6.3", "1", "接头"]),
    # 单位数编号后接 CJK（`6 混凝土分项工程`）：单个空白保留
    ("6 混凝土分项工程", ["6", "混凝土", "分项", "工程"]),
    # 单条无空白的普通路径（对照组）
    ("2 术语和符号 > 2.1 术语", ["2", "术语", "和", "符号", "2.1", "术语"]),
]


def test_breadcrumb_spacing_normalization_leaves_non_cjk_spaces_alone():
    """非词内空白形态：归一化逐字不动，面包屑 token 流与今天**完全一致**

    ⚠ 这里是**逐 token 等值**断言（不是「含某词」）：归一化若误伤任意一处，
    对应 token 会消失或变形，本用例立刻变红。
    """
    for path, expected in _UNTOUCHED_SPACING:
        assert normalize_cjk_spacing(path) == path, f"{path!r} 被误改（不该动的空白被折叠）"
        _st, bc = build_search_text("1.0.1", "标题", "正文", path)
        assert bc.split() == expected, f"{path!r} 的 token 流变了: {bc.split()}"
        # 与「不归一化」的旧口径逐 token 相同 —— 即这些形态的行为零变化
        assert bc == " ".join(tokenize(path))


def test_breadcrumb_collapses_intra_word_spaces_before_tokenizing():
    """面包屑列必须先折叠词内空白再分词，才能产出整词 `总则` 而非 `总`/`则`"""
    for spaced, collapsed, word in _SPACED_SECTIONS:
        _st, bc = build_search_text("1.0.1", "适用范围", "正文甲", spaced)
        assert bc == " ".join(tokenize(collapsed)), f"{spaced!r} → {bc!r}"
        assert word in bc.split(), f"{spaced!r} 的面包屑未产出整词 {word!r}: {bc!r}"

    # 全角空格形态同样产出整词
    _st, bc = build_search_text("1.0.1", "", "正文甲", "1　总　则")
    assert "总则" in bc.split()


def test_search_text_is_not_normalized_only_breadcrumb():
    """归一化只作用于面包屑列：search_text 不含面包屑，故不受影响

    护住「派生文本就地清洗」的边界——若有人顺手把归一化挪进 tokenize/查询侧，
    查询串与全库索引口径会同时漂移（`normalize_cjk_spacing` 只在面包屑列调用）。
    """
    title, content = "适用范围", "正文甲 适用于 城市 桥梁"
    st_spaced, _bc = build_search_text("1.0.1", title, content, "1 总 则")
    st_no_path, _ = build_search_text("1.0.1", title, content)
    assert st_spaced == st_no_path, "search_text 不得随 section_path（更不得随归一化）变化"
