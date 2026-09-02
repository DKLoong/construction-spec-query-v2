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
    """重建向量索引进度环元素与后台确认文案存在（百分比/轻提示触发路径）"""
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/maintenance")
    assert resp.status_code == 200
    assert "rebuild-overlay" in resp.text
    assert "rebuild-pct" in resp.text
    assert "startRebuild" in resp.text
    assert "可在后台进行，完成后将提醒你" in resp.text  # 更新后的确认文案
    assert "向量索引重建已完成" in resp.text  # 完成轻提示文案
    assert "/maintenance/rebuild-progress/" in resp.text  # 进度轮询端点
