"""健康检查「缺失向量索引」状态区分测试

表不存在 → 待重建（rebuild，非异常）；表存在但读取失败 → 需人工查日志（error）；
正常 → ok。
"""
from app.database import init_db
from app.maintenance.health_check import run_health_check


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "hc.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()


def _vm(res):
    return next(c for c in res["checks"] if c["key"] == "vector_missing")


def test_vector_table_missing_shows_rebuild(monkeypatch, tmp_path):
    """向量表整体不存在（全新 lance 目录）→ 待重建而非报错"""
    _setup(monkeypatch, tmp_path)
    vm = _vm(run_health_check())
    assert vm["severity"] == "rebuild"
    assert vm["count_text"] == "待重建"
    assert "待重建" in vm["status_text"]
    assert "重建向量索引" in vm["hint"]


def test_vector_read_failure_shows_error_hint(monkeypatch, tmp_path):
    """表存在但读取抛异常 → error，提示查服务端日志（区别于缺表待重建）"""
    _setup(monkeypatch, tmp_path)
    import app.search.vector_search as vsmod

    class _Broken:
        def _table_exists(self):
            return True

        def _get_table(self):
            raise RuntimeError("lance read broken")

    monkeypatch.setattr(vsmod, "VectorStore", _Broken)
    vm = _vm(run_health_check())
    assert vm["severity"] == "error"
    assert vm["count_text"] == "—"
    assert "读取失败" in vm["status_text"]
    assert "服务端日志" in vm["hint"]


def test_vector_empty_table_ok(monkeypatch, tmp_path):
    """表存在且空（空库无条文）→ 正常，不误入缺表/读失败分支"""
    _setup(monkeypatch, tmp_path)
    import pyarrow as pa
    import app.search.vector_search as vsmod

    class _EmptyStore:
        def _table_exists(self):
            return True

        def _get_table(self):
            return type("T", (), {
                "to_arrow": lambda self: pa.table(
                    {"clause_id": pa.array([], type=pa.int64())})
            })()

    monkeypatch.setattr(vsmod, "VectorStore", _EmptyStore)
    vm = _vm(run_health_check())
    assert vm["severity"] == "ok"
