"""参数设置面板 UI 冒烟：第 5 Tab 存在、按钮/提示/校验文案齐全"""
from app.database import init_db


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "pui.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()


def test_maintenance_page_has_params_tab(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/maintenance")
    assert resp.status_code == 200
    assert "参数设置" in resp.text
    assert "paramsManager" in resp.text
    # 按钮组（保存/应用/另存为）+ 默认方案提示 + 越界校验文案
    for token in ("保存", "应用", "另存为", "方案", "默认方案不可修改",
                  "您输入的参数超出可调范围，请重新输入"):
        assert token in resp.text, token
