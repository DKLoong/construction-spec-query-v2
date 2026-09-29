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


def test_health_result_states_one_time_rebuild_notice(auth_client, monkeypatch, tmp_path):
    """R6：本批改了写入向量的**内容**（text/embedding 加面包屑），而健康检查只查结构与计数
    ⇒ 察觉不到内容变更（当初那个只看「列是否存在」的小探针同样看不到，且已于 2026-09-29
    删除，见 TODOS T28）。

    此处不得把「全部 ✅」当「索引健康」——维护页必须明说「升级后需重建一次」，
    否则用户会把列结构就绪误读成索引已重建。
    """
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    resp = auth_client.post("/maintenance/health-check", data={})
    assert resp.status_code == 200
    assert "重建一次" in resp.text
    assert "面包屑" in resp.text
