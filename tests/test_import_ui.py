"""导入对话框校核 UI 模板渲染测试"""
from app.database import init_db


def test_import_dialog_has_version_check_button(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "iui1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    resp = auth_client.get("/")
    assert "校核有效性" in resp.text
    assert "仅现行" in resp.text  # 检索侧复选框同页存在（与 Task 4 并存不冲突）
