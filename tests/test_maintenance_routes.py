"""维护路由健康检查端点测试"""
import json
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path, name="m.db"):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / f"lance-{name}"))
    init_db()
    return db_path


def test_health_check_endpoint_returns_result(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)
    resp = auth_client.post("/maintenance/health-check")
    assert resp.status_code == 200
    assert "孤立无父级条文" in resp.text  # 渲染结果标签


def test_fix_all_endpoint(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/maintenance/fix-all")
    assert resp.status_code == 200


def test_health_check_writes_snapshot(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    auth_client.post("/maintenance/health-check")
    with get_db() as conn:
        row = conn.execute(
            "SELECT result FROM health_check_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None and "checks" in row["result"]
