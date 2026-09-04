from app.lexicon.store import LexiconRow
from app.lexicon.expand import build_expanded_match

G = [
    LexiconRow(1, "alias", "混凝土", ["砼", "混泥土"]),
    LexiconRow(2, "synonym", "坍落度", ["塌落度"]),
    LexiconRow(3, "alias", "混凝土结构", ["砼结构"]),  # 互为前缀组（长词优先）
]


def test_oov_variant_expands():
    # 俗词即使 jieba 切不出（此处整词命中词表）也展开
    out = build_expanded_match("塌落度试验", [LexiconRow(9, "alias", "坍落度", ["塌落度"])])
    assert '("坍落度" OR "塌落度")' in out and '"试验"' in out


def test_canonical_expansion_and_normal_token():
    out = build_expanded_match("砼强度等级", G)
    # 命中 alias 混凝土组 → OR；强度/等级为普通词 token AND 连接
    assert '("混凝土" OR "砼" OR "混泥土")' in out
    assert out.endswith('AND "强度" AND "等级"') or '"强度"' in out and '"等级"' in out
    assert out.count("OR") == 2


def test_longest_prefix_group_wins():
    # 词库同时含「混凝土」与「混凝土结构」：长词优先命中后者，不重复展开前者
    g = [LexiconRow(1, "alias", "混凝土", ["砼"]),
         LexiconRow(2, "alias", "混凝土结构", ["砼结构"])]
    out = build_expanded_match("混凝土结构施工", g)
    assert '("混凝土结构" OR "砼结构")' in out
    assert '("混凝土" OR "砼")' not in out


def test_no_group_delegates_to_original():
    import jieba
    from app.search.tokenize import build_match_query as orig
    kw = "钢筋混凝土强度"
    assert build_expanded_match(kw, []) == orig(kw)


def test_join_with_top_level_connector():
    # join_with 决定顶层连接符；用无标点输入验证，避免被 tokenize 的 \W+ 过滤干扰。
    # 引号转义 _quote 仅在「词本身含引号」时触发；jieba seg/词表词不含引号，故不设可测断言。
    out = build_expanded_match("砼 强度", G, "OR")
    assert ' OR ' in out and ' AND ' not in out
    out2 = build_expanded_match("砼 强度", G, "AND")
    assert ' AND ' in out2
    out3 = build_expanded_match("砼", G, "AND")
    assert out3.startswith("(") and out3.endswith(")")


def test_empty_canonical_never_enters_term_table():
    # 空 canonical 若进 term 表会 startswith('') 恒真 + i+=0 死循环；store._row_from
    # 对 canonical 原样透传无空值守卫，T8 validation 落地前可能为空串 → 必须滤空。
    from app.lexicon.expand import _terms_by_len
    g = [LexiconRow(1, "alias", "", ["砼"])]
    assert _terms_by_len(g) == [(0, "砼")]
