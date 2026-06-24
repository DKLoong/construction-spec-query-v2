from fastapi.testclient import TestClient


def test_login_page_loads(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "登录" in resp.text or "login" in resp.text.lower()


def test_login_success(client, monkeypatch, tmp_path):
    from app.auth import hash_password
    from app.database import init_db, get_db
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    pwd_hash = hash_password("test123")
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", pwd_hash))
    resp = client.post("/login", data={"username": "admin", "password": "test123"},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_login_failure(client, monkeypatch, tmp_path):
    from app.auth import hash_password
    from app.database import init_db, get_db
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    pwd_hash = hash_password("test123")
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", pwd_hash))
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 200
    assert "用户名或密码错误" in resp.text


def test_protected_page_redirects_to_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)
