"""检索状态过滤（status_filter）测试"""
from tests.conftest import setup_search_data
from app.database import get_db, init_db


def _setup_with_obsolete(conn):
    setup_search_data(conn)
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
        ("GBJ 10-1989", "旧混凝土规范", "废止"),
    )
    sid = conn.execute("SELECT id FROM specifications WHERE code = 'GBJ 10-1989'").fetchone()["id"]
    conn.execute(
        "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
        (sid, "1.0.1", "旧条文", "本条文来自已废止规范。"),
    )


def _setup_db(auth_client, monkeypatch, tmp_path, name):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _setup_with_obsolete(conn)
    return auth_client


def test_search_status_filter_current_excludes_obsolete(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf1.db")
    resp = client.get("/search?all=1&status_filter=现行")
    assert "旧条文" not in resp.text
    assert "钢筋" in resp.text


def test_search_status_filter_current_and_revising(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf2.db")
    conn_ctx = None
    with get_db() as conn:
        conn.execute("UPDATE specifications SET status = '修订中' WHERE code = 'GB 50204'")
    resp = client.get("/search?all=1&status_filter=现行,修订中")
    # 白名单含 修订中 → GB 50204(现改修订中) 保留；废止(GBJ 10-1989) 被过滤
    assert "旧条文" not in resp.text
    assert "钢筋" in resp.text


def test_search_status_filter_empty_returns_all(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf3.db")
    resp = client.get("/search?all=1&status_filter=")
    assert "旧条文" in resp.text
    assert "钢筋" in resp.text


def test_search_no_status_filter_param_backward_compat(auth_client, monkeypatch, tmp_path):
    """不带 status_filter 参数 → 不过滤（旧行为兼容）"""
    client = _setup_db(auth_client, monkeypatch, tmp_path, "sf4.db")
    resp = client.get("/search?all=1")
    assert "旧条文" in resp.text
