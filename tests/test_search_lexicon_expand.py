"""检索词库扩展接线测试：alias 俗词输入经词库扩展命中规范条文；开关关退回纯原词。

隔离说明：向量路径用空 lance 目录阻断（LANCE_DB_PATH 覆盖 → 无 clause_embeddings 表
→ VectorStore.search 返回 []），保证用例聚焦 SQL/FTS 扩展，不触发真实 embedding。
（brief 原稿 monkeypatch hybrid_search.VectorStore=None 对当前实现是 no-op——函数体用
`from app.search.vector_search import VectorStore` 局部导入，不读模块属性，故改为本仓既有
模式 app.search.vector_search.LANCE_DB_PATH 覆盖。）
"""
from app.database import init_db, get_db
from app.models import SearchQuery
from app.search.hybrid_search import hybrid_search


def _seed(conn):
    """预置一条含「混凝土」的条文（search_text 已分词，砼 不在其中）"""
    conn.execute("INSERT INTO specifications(code,title) VALUES ('GB 50204-2015','混凝土结构')")
    sid = conn.execute("SELECT id FROM specifications").fetchone()["id"]
    conn.execute(
        "INSERT INTO clauses(spec_id, clause_no, title, content, search_text) VALUES (?,?,?,?,?)",
        (sid, "4.1", "强度", "混凝土强度不应低于设计值",
         "混凝土 强度 不应 低于 设计 值 4.1"))


def test_alias_input_recalls_canonical_document(monkeypatch, tmp_path):
    """输入俗词砼 → 词库扩展出混凝土 → 命中含混凝土条文（主方向）"""
    import app.database as _db
    from app.lexicon import store
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "x.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance_x"))
    store.invalidate_lexicon_caches()  # 清词库/检索缓存，确保按 x.db 加载
    init_db()
    with get_db() as conn:
        _seed(conn)

    res, _ = hybrid_search(SearchQuery(keyword="砼"))
    assert any("混凝土" in r["content"] for r in res)


def test_expand_switch_off_returns_empty(monkeypatch, tmp_path):
    """search.lexicon_expand=0：扩展关闭，纯原词「砼」无命中（退化护栏）"""
    import app.database as _db
    import app.params.registry as registry
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "y.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance_y"))
    init_db()
    with get_db() as conn:
        _seed(conn)
    monkeypatch.setattr(
        registry, "get_param_int",
        lambda k: 0 if k == "search.lexicon_expand" else 1)

    res, _ = hybrid_search(SearchQuery(keyword="砼"))
    assert res == []  # 开关关：纯原词「砼」无条文命中


# ═══════════════════════════════════════════
# spec §十 测试闭环（final-review fix）
# ═══════════════════════════════════════════

def test_vector_input_unchanged(monkeypatch, tmp_path):
    """向量输入原文不变（spec 边界 1）：传给 VectorStore.search 的关键词与原始 keyword 逐字节相等。

    Fake VectorStore 捕获入参并返回空列表（不污染 RRF 结果），monkeypatch 替换
    hybrid_search 的局部导入点 app.search.vector_search.VectorStore（本仓既有隔离模式）。
    """
    import app.database as _db
    from app.lexicon import store
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "v.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_v"))
    store.invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        _seed(conn)

    captured = {}

    class _FakeVectorStore:
        def search(self, query_text, top_k=10):
            captured["query_text"] = query_text
            captured["top_k"] = top_k
            return []

    monkeypatch.setattr("app.search.vector_search.VectorStore", _FakeVectorStore)

    hybrid_search(SearchQuery(keyword="砼"))
    # 向量输入不得被词库扩展改写（不得变成「混凝土」等展开产物）
    assert captured.get("query_text") == "砼"


def test_jieba_probe_slang_is_independent_token():
    """探针（spec §5.5 反向召回可行性）：俗词「砼」被 jieba 切成独立 token。

    实证依据（本机实际运行）：jieba 0.42.1 精确模式（cut_all=False）+ 固定词典，
    tokenize('砼') == ['砼']、tokenize('混凝土强度砼') == ['混凝土','强度','砼']。
    结论：索引侧 search_text 含独立 token「砼」→ 反向召回（输入规范词召回含俗词条文）可行。
    """
    from app.search.tokenize import build_search_text, tokenize
    assert "砼" in tokenize("混凝土强度砼")
    st = build_search_text("4.1", "强度", "混凝土强度砼不应低于设计值")
    assert "砼" in st.split(), "search_text 中俗词「砼」须为独立 token"


def test_reverse_canonical_recalls_slang_document(monkeypatch, tmp_path):
    """反向（探针实证通过后承诺）：输入规范词「混凝土」经扩展召回仅含俗词「砼」的条文。

    条文 search_text 只含「砼」token（不含「混凝土」），靠 alias 混凝土={砼} 组内 OR
    展开 (混凝土 OR 砼) 命中，锁定 spec §5.5 反向方向。
    """
    import app.database as _db
    from app.lexicon import store
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_r"))
    store.invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications(code,title) VALUES ('GB 50204-2015','混凝土结构')")
        sid = conn.execute("SELECT id FROM specifications").fetchone()["id"]
        conn.execute(
            "INSERT INTO clauses(spec_id, clause_no, title, content, search_text) VALUES (?,?,?,?,?)",
            (sid, "4.2", "浇筑", "砼强度不应低于设计值",
             "砼 强度 不应 低于 设计 值 4.2"))

    res, _ = hybrid_search(SearchQuery(keyword="混凝土"))
    assert any(r["clause_no"] == "4.2" for r in res)
