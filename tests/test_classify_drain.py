# -*- coding: utf-8 -*-
"""#3a 运行AI分类 drain 模式测试：内部按 batch_size 分批直到该维待处理清空"""
import types

from app.ai.classifier_ai import process_pending_batches
from app.classifier.batch_queue import add_to_queue
from app.database import init_db, get_db


def _seed(conn, n=25):
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES ('GB 50204', '测试规范', '现行')",
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    ids = []
    for i in range(n):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
            (sid, f"1.0.{i+1}", f"第{i+1}条 混凝土模板钢筋施工"),
        )
        ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return ids


def _pending(conn):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM classification_queue WHERE status='pending'"
    ).fetchone()["n"]


def _fake_backend():
    class FakeBackend:
        def is_available(self):
            return True

        def classify_batch_sync(self, batch, dim, candidate_labels=None):
            # 每条返回高置信采纳标签 → apply_ai_results 置 auto_adopted
            return [
                types.SimpleNamespace(clause_id=b["clause_id"], label="测试材料", confidence=0.95)
                for b in batch
            ]

    return FakeBackend


def _run_drain(monkeypatch, tmp_path, drain, n=25):
    db_path = tmp_path / "drain.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        ids = _seed(conn, n=n)
    for cid in ids:
        add_to_queue(cid, "dim6", 0.35)

    monkeypatch.setattr("app.ai.classifier_ai.get_backend", lambda name: _fake_backend()())
    kwargs = {"force": True, "drain": drain}
    total = process_pending_batches("claude", **kwargs)
    with get_db() as conn:
        remain = _pending(conn)
    return total, remain


def test_drain_true_clears_all_pending(monkeypatch, tmp_path):
    """drain=True：25 条 > batch_size(20)，分两批全部处理完，无残留"""
    total, remain = _run_drain(monkeypatch, tmp_path, drain=True, n=25)
    assert total == 25
    assert remain == 0


def test_drain_false_keeps_single_batch_semantics(monkeypatch, tmp_path):
    """drain 默认(False)：仅处理一批 batch_size=20，留 5 条（spec 重跑等调用不受影响）"""
    total, remain = _run_drain(monkeypatch, tmp_path, drain=False, n=25)
    assert total == 20
    assert remain == 5


def test_drain_true_no_pending_noop(monkeypatch, tmp_path):
    """队列为空时 drain 正常返回 0"""
    db_path = tmp_path / "drain_empty.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    monkeypatch.setattr("app.ai.classifier_ai.get_backend", lambda name: _fake_backend()())
    assert process_pending_batches("claude", force=True, drain=True) == 0
