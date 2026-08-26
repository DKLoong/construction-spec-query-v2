"""jieba 预分词工具（app/search/tokenize.py）单元测试

验证：中文词组分词、纯标点过滤、search_text 构建（含 clause_no 原文追加）、
FTS MATCH 查询串构造、确定性（一致性基础）。
"""

from app.search.tokenize import tokenize, build_search_text, build_match_query


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


def test_build_search_text_combines_title_content():
    """search_text = jieba(标题+正文) 空格连接"""
    st = build_search_text("", "模板设计", "模板及其支架应根据工程结构形式进行设计。")
    assert "模板" in st
    assert "设计" in st
    assert "支架" in st
    # 分词用空格连接
    assert st == " ".join(st.split())


def test_build_search_text_appends_clause_no_raw():
    """clause_no 原文追加（不走 jieba，保留编号检索能力）"""
    st = build_search_text("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。")
    assert st.endswith("5.1.1"), f"clause_no 应原样追加到 search_text 末尾，实际: {st!r}"


def test_build_search_text_empty_returns_space():
    """空文本返回占位空格，避免 FTS5 索引 NULL 报 datatype mismatch"""
    assert build_search_text("", "", "") == " "


def test_build_search_text_deterministic():
    """同输入同输出（分词一致性：索引/查询两侧可稳定对齐）"""
    a = build_search_text("2.1.4", "套筒", "套筒 coupler or sleeve 是钢筋机械连接的关键部件。")
    b = build_search_text("2.1.4", "套筒", "套筒 coupler or sleeve 是钢筋机械连接的关键部件。")
    assert a == b


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
