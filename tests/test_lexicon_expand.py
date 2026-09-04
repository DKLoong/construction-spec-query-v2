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


def test_join_or_and_quote_escape():
    out = build_expanded_match('抗"震"砼', [LexiconRow(1, "alias", "混凝土", ["砼"])], "OR")
    assert '"砼"' in out  # 词条词参与；引号转义沿用原版 quote
    out2 = build_expanded_match("砼", G, "AND")
    assert out2.startswith("(") and out2.endswith(")")
