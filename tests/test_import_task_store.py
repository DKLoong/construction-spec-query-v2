"""导入任务台账落库：CRUD 往返、只写传入列、无进程内状态

「无进程内状态」是本文件的核心断言：每次校验都**另开一个连接**，因此"写完能读到"
在语义上等价于"重启后仍在"——这是持久化唯一的真实判据（探针证明不了它：探针跑在
一个已运行的服务上，刷新页面并不会重启服务）。
"""
import sqlite3
import time

import pytest

from app.routes import import_routes as ir
from tests.conftest import import_task_row


@pytest.fixture
def _db(tmp_path, monkeypatch):
    from app.database import init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "store.db"))
    init_db()
    return tmp_path / "store.db"


def test_create_and_get_roundtrip(_db):
    ir.create_task("aaaabbbb", owner="admin")

    task = import_task_row("aaaabbbb")

    assert task["task_id"] == "aaaabbbb"
    assert task["status"] == "uploading"
    assert task["progress"] == 0
    assert task["message"] == "正在上传..."
    assert task["owner"] == "admin"
    assert isinstance(task["updated_at"], float)


def test_get_missing_returns_none(_db):
    assert ir._get_task("nope0000") is None


def test_update_returns_false_for_missing_task(_db):
    """A 组确立的语义：任务不存在返回 False，调用方据此收工（不得抛异常）"""
    assert ir._update_task("nope0000", progress=50) is False


def test_update_refreshes_updated_at(_db):
    ir.create_task("aaaabbbb")
    before = import_task_row("aaaabbbb")["updated_at"]

    time.sleep(0.01)
    assert ir._update_task("aaaabbbb", progress=50) is True

    assert import_task_row("aaaabbbb")["updated_at"] > before


def test_update_rejects_unknown_column(_db):
    """列名要拼进 SET 子句，必须有白名单兜底（防注入，也防字段名打错后静默写不进去）"""
    ir.create_task("aaaabbbb")

    with pytest.raises(ValueError):
        ir._update_task("aaaabbbb", status_typo="processing")


def test_update_writes_only_passed_columns(_db, monkeypatch):
    """UPDATE 只写传入字段——否则 OCR 期间每次只改 message 都会重写整行 md_text

    用 sqlite3 的 trace 回调抓**实际执行的 SQL**（不是读源码字符串）：它才是真正
    决定"写多少"的东西。
    """
    traces = []
    real_connect = sqlite3.connect

    ir.create_task("aaaabbbb")
    ir._update_task("aaaabbbb", md_text="正文" * 100)

    def traced(path, *a, **kw):
        conn = real_connect(path, *a, **kw)
        conn.set_trace_callback(traces.append)
        return conn

    monkeypatch.setattr("app.database.sqlite3.connect", traced)
    traces.clear()
    ir._update_task("aaaabbbb", message="正在 OCR 识别...")

    updates = [s for s in traces if s.strip().upper().startswith("UPDATE IMPORT_TASKS")]
    assert updates, f"没抓到 UPDATE 语句：{traces!r}"
    assert all("md_text" not in s for s in updates), \
        f"UPDATE 覆盖了未传入的 md_text 列：{updates!r}"


def test_md_text_survives_message_update(_db):
    ir.create_task("aaaabbbb")
    big = "正" * 200000
    ir._update_task("aaaabbbb", md_text=big)

    ir._update_task("aaaabbbb", message="正在写入向量索引（500/3000）…")

    assert import_task_row("aaaabbbb")["md_text"] == big


def test_delete_and_iter(_db):
    ir.create_task("aaaabbbb")
    ir.create_task("ccccdddd")
    assert {t["task_id"] for t in ir.iter_tasks()} == {"aaaabbbb", "ccccdddd"}

    ir.delete_task("aaaabbbb")

    assert ir._get_task("aaaabbbb") is None
    assert {t["task_id"] for t in ir.iter_tasks()} == {"ccccdddd"}


def test_delete_missing_is_noop(_db):
    ir.delete_task("nope0000")      # 幂等：不得抛异常


def test_row_visible_from_new_connection(_db):
    """核心判据：写入后**另开连接**能读到 = 没有进程内状态 = 重启后仍在"""
    ir.create_task("eeeeffff")
    ir._update_task("eeeeffff", status="review_needed", progress=50, md_text="1.0.1 条文")

    conn = sqlite3.connect(str(_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, md_text FROM import_tasks WHERE task_id = ?", ("eeeeffff",)
    ).fetchone()
    conn.close()

    assert row is not None, "另开连接读不到 → 说明还依赖进程内状态"
    assert row["status"] == "review_needed"
    assert row["md_text"] == "1.0.1 条文"
