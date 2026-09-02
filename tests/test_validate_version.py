"""导入版本校核接口测试（AI 输出解析 + 不可用兜底）"""
from unittest.mock import AsyncMock, MagicMock
from app.database import get_db, init_db


class _FakeResp:
    def __init__(self, content): self.content, self.success, self.error = content, True, None


def _mock_backend(monkeypatch, content):
    """patch app.ai.cli_client.get_backend（validate_version 函数内 from cli_client import）"""
    import app.ai.cli_client as cc
    fake = MagicMock()
    fake.is_available.return_value = True
    fake.ask = AsyncMock(return_value=_FakeResp(content))
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    return fake


def _setup_db(auth_client, monkeypatch, tmp_path, name):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    return auth_client


def test_validate_version_returns_status(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "vv1.db")
    _mock_backend(monkeypatch, '{"status":"废止","replaced_by_code":"GB 50010-2015","corrected_code":"GBT 50107-2010","corrected_title":"燃气工程制图标准","ai_available":true}')
    resp = client.post("/import/validate-version", json={"code": "GBT 50107-2010", "title": "燃气工程制图标准"})
    data = resp.json()
    assert data["status"] == "废止"
    assert data["replaced_by_code"] == "GB 50010-2015"
    # corrected_code 应已被 normalize_spec_code 兜底规范
    assert data["corrected_code"] == "GB/T 50107-2010"


def test_validate_version_ai_unavailable_fallback(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "vv2.db")
    import app.ai.cli_client as cc
    fake = MagicMock()
    fake.is_available.return_value = False
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    resp = client.post("/import/validate-version", json={"code": "GB 50010", "title": "x"})
    data = resp.json()
    assert data["status"] == "现行"
    assert data["ai_available"] is False


def test_validate_version_ai_exception_fallback(auth_client, monkeypatch, tmp_path):
    client = _setup_db(auth_client, monkeypatch, tmp_path, "vv3.db")
    import app.ai.cli_client as cc
    fake = MagicMock()
    fake.is_available.return_value = True
    fake.ask.side_effect = RuntimeError("timeout")
    monkeypatch.setattr(cc, "get_backend", lambda *a, **k: fake)
    resp = client.post("/import/validate-version", json={"code": "GB 50010", "title": "x"})
    data = resp.json()
    assert data["status"] == "现行"
    assert data["ai_available"] is False
