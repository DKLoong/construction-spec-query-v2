"""日志管理界面（/maintenance 四 Tab）UI 冒烟测试"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "logs_ui.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()
    return db_path


def test_maintenance_page_has_logs_tabs(auth_client, monkeypatch, tmp_path):
    """/maintenance 含日志管理 + QA 日志两 Tab，日志面板含筛选/导出/手动清理"""
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/maintenance")
    assert resp.status_code == 200
    assert "日志管理" in resp.text and "QA 日志" in resp.text
    assert "导出异常日志" in resp.text
    assert "手动清理" in resp.text


def test_maintenance_logs_fragment_lists_rows(auth_client, monkeypatch, tmp_path):
    """日志列表 fragment 渲染种子行"""
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO system_logs (category, level, action, username) "
            "VALUES ('spec', 'INFO', 'UI 埋点样例', 'admin')")
    resp = auth_client.get("/maintenance/logs")
    assert resp.status_code == 200
    assert "UI 埋点样例" in resp.text


def test_maintenance_qa_logs_fragment(auth_client, monkeypatch, tmp_path):
    """QA 日志 fragment 渲染 qa_request_logs 行"""
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO qa_request_logs (question, mode, backend) "
            "VALUES ('如何做屋面防水？', 'qa', 'bge')")
    resp = auth_client.get("/maintenance/qa-logs")
    assert resp.status_code == 200
    assert "如何做屋面防水" in resp.text
