"""FTS5 optimize 端点测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "fts.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)


def test_fts_optimize_endpoint(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/maintenance/fts-optimize")
    assert resp.status_code == 200
    assert "optimize" in resp.text.lower() or "完成" in resp.text


def test_fts_optimize_sql_runs(monkeypatch, tmp_path):
    """optimize 命令真实可执行"""
    _setup(monkeypatch, tmp_path)
    from app.database import get_connection
    conn = get_connection()
    try:
        conn.execute("INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')")
        conn.commit()
    finally:
        conn.close()
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM clauses_fts").fetchone()[0]
    assert total >= 0
