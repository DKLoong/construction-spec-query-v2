import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def test_dir(tmp_path):
    """提供临时目录作为测试用的 data 根目录"""
    return tmp_path


@pytest.fixture
def app():
    from app.main import app
    return app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def auth_client(client, monkeypatch, tmp_path):
    """已认证的 TestClient"""
    from app.auth import hash_password
    from app.database import init_db, get_db
    db_path = tmp_path / "test_auth.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    pwd_hash = hash_password("test123")
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", pwd_hash))
    resp = client.post("/login", data={"username": "admin", "password": "test123"},
                       follow_redirects=False)
    # Set cookie on client for subsequent requests
    if "set-cookie" in resp.headers:
        client.cookies.set("access_token", resp.cookies.get("access_token"))
    return client
