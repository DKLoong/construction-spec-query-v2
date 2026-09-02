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
    """重建进度环元素、确认文案与全局追踪器引入（跨页完成轻提示在 rebuild.js）"""
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/maintenance")
    assert resp.status_code == 200
    assert "rebuild-overlay" in resp.text
    assert "rebuild-pct" in resp.text
    assert "startRebuild" in resp.text
    assert "RebuildTracker" in resp.text  # 接全局追踪器
    assert "可在后台进行，完成后将提醒你" in resp.text  # 更新后的确认文案
    assert "/static/components/rebuild.js" in resp.text  # 全局跨页追踪器已引入
    assert "rebuild-finished" in resp.text  # 完成事件监听（刷新健康结果）
    # health-result 自动加载健康检查（切回维护页不空白）
    assert 'hx-post="/maintenance/health-check" hx-trigger="load"' in resp.text
