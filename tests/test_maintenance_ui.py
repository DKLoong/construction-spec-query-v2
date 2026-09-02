"""维护界面 UI 模板渲染测试"""
from app.database import init_db


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "ui.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()


def test_tree_panel_has_maintenance_button(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/")
    assert "🛠 维护" in resp.text


def test_maintenance_page_has_three_tabs(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/maintenance")
    assert resp.status_code == 200
    assert "健康检查" in resp.text
    assert "导出备份" in resp.text
    assert "日志管理" in resp.text


def test_maintenance_page_has_rebuild_overlay(auth_client, monkeypatch, tmp_path):
    """重建向量索引同步动画元素与文案存在（确认后 spinner，完成自动消失）"""
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/maintenance")
    assert resp.status_code == 200
    assert "rebuild-overlay" in resp.text
    assert "正在同步向量索引" in resp.text
    assert "/maintenance/rebuild-vectors" in resp.text
