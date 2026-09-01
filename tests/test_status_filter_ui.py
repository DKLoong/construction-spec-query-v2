"""检索状态复选框模板渲染测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def test_search_page_has_status_checkboxes(auth_client, monkeypatch, tmp_path):
    """左侧面板包含「仅现行」「修订中」两个复选框"""
    db_path = tmp_path / "sfu1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    resp = auth_client.get("/")
    assert "仅现行" in resp.text
    assert "修订中" in resp.text


def test_search_results_with_status_filter_param(auth_client, monkeypatch, tmp_path):
    """/search 携带 status_filter 参数正常返回结果（复选框在左侧面板，不在此断言）"""
    db_path = tmp_path / "sfu2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    resp = auth_client.get("/search?keyword=钢筋&status_filter=现行")
    assert resp.status_code == 200
    assert "钢筋" in resp.text
