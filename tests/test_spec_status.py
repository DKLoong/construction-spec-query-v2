"""规范状态修改路由测试"""
from app.database import get_db, init_db


def _setup(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES (?, ?)", ("GB 50204", "混凝土规范"))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_update_spec_status(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "废止"})
    assert resp.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT status FROM specifications WHERE id = ?", (sid,)).fetchone()
        assert row["status"] == "废止"


def test_update_spec_status_invalid_value_rejected(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "无效状态"})
    assert resp.status_code == 400


def test_specs_table_shows_status_column(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
        conn.execute("UPDATE specifications SET status = '废止' WHERE id = ?", (sid,))
    resp = auth_client.get("/specs/list")
    assert "状态" in resp.text
    assert "废止" in resp.text
