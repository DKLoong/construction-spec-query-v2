"""导入对话框校核 UI 模板渲染测试"""
from app.database import init_db


def test_import_dialog_has_version_check_button(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "iui1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    resp = auth_client.get("/")
    assert "校核有效性" in resp.text
    assert "仅现行" in resp.text  # 检索侧复选框同页存在（与 Task 4 并存不冲突）


def test_import_dialog_renders_close_button_without_overlay_close(auth_client, monkeypatch, tmp_path):
    """渲染结果里必须有右上角 ×，且遮罩不再支持点击关闭。

    与 `test_templates.py::test_import_dialog_closes_only_via_close_button` 互补：那条验
    **模板文件内容**，这条验**渲染结果**——防「模板里写了但被条件分支跳过或被转义吃掉」
    这类只在渲染层才暴露的问题。
    """
    db_path = tmp_path / "iui2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    html = auth_client.get("/").text
    assert "dialog-close" in html, "渲染结果里应有右上角关闭按钮"
    assert "dialog-box--closable" in html, "关闭按钮需要定位上下文类（.dialog-box 本身不加 position）"
    # 必须断言到具体方法名：同一页还渲染着**设置弹窗**，它的 `@click.self="close()"`
    # 是另一回事（设置弹窗不承载后台任务，误触无副作用），不能被这条守卫误伤
    assert '@click.self="closeDialog()"' not in html, "导入弹窗遮罩不得再支持点击外部关闭"
