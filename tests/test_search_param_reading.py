"""search 参数读取改造测试：hybrid_search 在 cache-miss 分支读取 DB 覆盖参数"""
from app.models import SearchQuery
from app.search.hybrid_search import hybrid_search, clear_search_cache
from app.database import init_db
from app.params import registry


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    registry.clear_param_cache()
    clear_search_cache()


def test_hybrid_search_reads_search_params_once(monkeypatch, tmp_path):
    """空库触发 cache-miss 全流程，确认四个 search 参数被读取"""
    _setup(monkeypatch, tmp_path)
    called = []
    orig_int, orig_float = registry.get_param_int, registry.get_param_float

    def spy_int(k):
        called.append(k)
        return orig_int(k)

    def spy_float(k):
        called.append(k)
        return orig_float(k)

    monkeypatch.setattr(registry, "get_param_int", spy_int)
    monkeypatch.setattr(registry, "get_param_float", spy_float)

    results, total = hybrid_search(SearchQuery(keyword="绝对不存在的词xyz", per_page=10))
    assert total == 0 and results == []
    for key in ("search.vector_top_k", "search.vector_l2_threshold",
                "search.rrf_k", "search.rerank_top_n"):
        assert key in called, key


def test_hybrid_search_accepts_db_overrides(monkeypatch, tmp_path):
    """检索参数被 DB 覆盖后流程不报错（值经注册表 clamp/解析生效）"""
    _setup(monkeypatch, tmp_path)
    with registry._db.get_db() as conn:
        for k, v in (("search.vector_top_k", "3"),
                     ("search.vector_l2_threshold", "0.5"),
                     ("search.rrf_k", "90")):
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    registry.clear_param_cache()
    results, total = hybrid_search(SearchQuery(keyword="钢筋", per_page=10))
    assert total == 0  # 空库无结果，但流程已按覆盖参数执行（未抛错）
