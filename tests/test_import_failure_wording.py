"""导入失败的提示必须说明「本次未完成、需重跑」

背景：`_process_import_phase2` 的失败分支原先只写 `status="error"` +
原始异常串（如 `database is locked`），读起来像一次可以忽略的偶发错误。
实际后果重得多：该分支的 `conn.rollback()` **只覆盖 SQLite 事务**——
LanceDB 的向量写入不在事务里（向量在 commit 之前就已写入），失败后 SQLite
回滚、向量行却留在表里，留下一个**常驻半成品态**（维护页随后报
「缺失向量索引」，只有整份文档重跑一遍才会被覆盖）。

故失败文案必须明确两点：本次导入**未完成** + 需要**重跑**；并且不得吞掉
原始原因（否则用户与运维都无从判断是哪儿失败）。文案本身**不带**「导入失败」
前缀——`import_progress.html` 的 error 分支已经渲染「导入失败: {{ message }}」。
"""
from app.database import init_db
from app.routes import import_routes as ir


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "fw.db"))
    monkeypatch.setattr(ir, "OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(ir, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()


def _fail_after_spec_insert(monkeypatch) -> None:
    """在「规范已 INSERT 但事务未 commit」之后抛错。

    故意选这个时点：它正是会产生「SQLite 回滚 + 向量/文件残留」半成品态的
    失败形态（`_link_replacement` 紧随 specifications INSERT）。
    """

    def _boom(conn, spec_id, replaced_by_code, status):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ir, "_link_replacement", _boom)


def test_failure_message_says_incomplete_and_must_rerun(monkeypatch, tmp_path):
    """失败文案：必须同时含「未完成」与「重跑」两个语义，且保留原始原因"""
    _setup(monkeypatch, tmp_path)
    _fail_after_spec_insert(monkeypatch)
    ir.progress_store["fw000001"] = {"status": "processing", "progress": 70, "owner": "t"}
    try:
        ir._process_import_phase2(
            "fw000001", "# 第1章\n5.1.1 条文内容测试。\n", "失败措辞测试规范",
            "GB/T 66666-2020", str(tmp_path / "f.md"), "hash_fw", "现行", "")
        entry = dict(ir.progress_store["fw000001"])
    finally:
        ir.progress_store.pop("fw000001", None)

    assert entry["status"] == "error", f"失败任务的 status 应为 error: {entry}"
    msg = entry["message"]
    assert "未完成" in msg, f"失败文案未说明本次导入未完成: {msg!r}"
    assert "重跑" in msg, f"失败文案未告知需重跑（半成品态需整份重导覆盖）: {msg!r}"
    assert "database is locked" in msg, f"失败文案吞掉了原始原因: {msg!r}"
    # `import_progress.html` 的 error 分支渲染「导入失败: {{ message }}」，
    # 文案自带同一前缀会显示成「导入失败: 导入失败，…」
    assert "导入失败" not in msg, f"文案与模板的「导入失败:」前缀重复: {msg!r}"
