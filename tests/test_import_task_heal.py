"""启动自愈：被进程死亡打断的任务必须给出明确结论，而不是永远转圈

uploading/processing 的后台线程随进程消失，任务**不可能**再推进（用户裁定不自动
重跑：Phase 1 重跑会真调外部 OCR 且计费，Phase 2 重跑会重复入库）。
"""
import time

import pytest

from app.routes import import_routes as ir
from tests.conftest import import_task_row


@pytest.fixture
def _db(tmp_path, monkeypatch):
    from app.database import init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "heal.db"))
    init_db()
    return tmp_path / "heal.db"


def test_heal_marks_running_states_as_error(_db):
    ir.create_task("aaaa0001")                                      # uploading
    ir.create_task("aaaa0002")
    ir._update_task("aaaa0002", status="processing", progress=20)    # processing

    assert ir.heal_interrupted_tasks() == 2

    for tid in ("aaaa0001", "aaaa0002"):
        task = import_task_row(tid)
        assert task["status"] == "error"
        assert task["progress"] == 0
        assert "服务重启导致本次导入中断" in task["message"]


def test_heal_leaves_review_needed_alone(_db):
    """这是本设计的主要收益：OCR 已完成、待审查的任务**不能**被自愈碰掉"""
    ir.create_task("aaaa0003")
    ir._update_task("aaaa0003", status="review_needed", progress=50, md_text="1.0.1 正文")

    assert ir.heal_interrupted_tasks() == 0

    task = import_task_row("aaaa0003")
    assert task["status"] == "review_needed"
    assert task["md_text"] == "1.0.1 正文"


def test_heal_leaves_terminal_states_alone(_db):
    ir.create_task("aaaa0004")
    ir._update_task("aaaa0004", status="done", progress=100, message="导入完成")
    ir.create_task("aaaa0005")
    ir._update_task("aaaa0005", status="error", progress=0, message="本次未完成，请重跑：X")

    assert ir.heal_interrupted_tasks() == 0
    assert import_task_row("aaaa0004")["message"] == "导入完成"
    assert import_task_row("aaaa0005")["message"] == "本次未完成，请重跑：X"


def test_heal_refreshes_updated_at(_db):
    """必须刷新 updated_at：否则中断很久的任务在重启瞬间就满足终态 TTL 被清理器删掉，
    用户刚看到提示、转身条目已消失——等于把"静默丢失"推迟几秒。"""
    from app.database import get_db
    ir.create_task("aaaa0006")
    ir._update_task("aaaa0006", status="processing")
    old = time.time() - 10 * 86400
    with get_db() as conn:
        conn.execute("UPDATE import_tasks SET updated_at = ? WHERE task_id = ?",
                     (old, "aaaa0006"))

    ir.heal_interrupted_tasks()

    assert import_task_row("aaaa0006")["updated_at"] > old


def test_heal_is_idempotent(_db):
    ir.create_task("aaaa0007")
    ir._update_task("aaaa0007", status="processing")

    assert ir.heal_interrupted_tasks() == 1
    assert ir.heal_interrupted_tasks() == 0      # 第二遍无可改状态
