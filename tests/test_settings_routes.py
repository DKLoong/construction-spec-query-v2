import pytest


def test_get_settings_requires_auth(client):
    """未登录不能访问设置"""
    resp = client.get("/settings", follow_redirects=False)
    assert resp.status_code == 302


def test_get_settings_returns_json(auth_client, monkeypatch, tmp_path):
    """获取设置返回 JSON"""
    db_path = tmp_path / "test_sett.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ocr.access_token', 'abc')"
        )

    resp = auth_client.get("/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ocr.access_token"] == "abc"


def test_put_settings_saves_values(auth_client, monkeypatch, tmp_path):
    """批量保存设置"""
    db_path = tmp_path / "test_sett2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.put("/settings", json={
        "ocr.access_token": "new-token",
        "ai.backend": "deepseek",
    })
    assert resp.status_code == 200

    # 验证持久化
    resp2 = auth_client.get("/settings")
    data2 = resp2.json()
    assert data2["ocr.access_token"] == "new-token"
    assert data2["ai.backend"] == "deepseek"


def test_put_settings_removes_old_key(auth_client, monkeypatch, tmp_path):
    """更新后旧值被覆盖"""
    db_path = tmp_path / "test_sett3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.backend', 'claude')"
        )

    resp = auth_client.put("/settings", json={"ai.backend": "kimi"})
    assert resp.status_code == 200

    resp2 = auth_client.get("/settings")
    assert resp2.json()["ai.backend"] == "kimi"


def test_test_ocr_no_token(auth_client, monkeypatch, tmp_path):
    """无 token 时测试 OCR 连接返回错误"""
    db_path = tmp_path / "test_sett4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.post("/settings/test-ocr")
    assert resp.status_code == 400
    assert "请先配置" in resp.json()["detail"]


def test_test_ai_no_key(auth_client, monkeypatch, tmp_path):
    """无 api_key 时测试 AI 连接返回错误"""
    db_path = tmp_path / "test_sett5.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.post("/settings/test-ai", json={
        "backend": "deepseek", "api_key": "",
    })
    assert resp.status_code == 400
