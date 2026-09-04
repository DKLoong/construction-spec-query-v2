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
