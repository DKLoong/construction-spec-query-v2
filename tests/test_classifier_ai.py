from app.ai.classifier_ai import process_pending_batches
from app.database import init_db, get_db
from app.classifier.batch_queue import add_to_queue


def setup_data(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '测试规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i in range(25):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
            (spec_id, f"1.0.{i+1}", f"第{i+1}条 混凝土模板钢筋施工"),
        )


def test_process_pending_batches_no_cli(monkeypatch, tmp_path):
    """CLI 不可用时应优雅降级（不调用 real CLI）"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_data(conn)
        clause_ids = [r["id"] for r in conn.execute("SELECT id FROM clauses LIMIT 22")]
    for cid in clause_ids[:20]:
        add_to_queue(cid, "dim6", 0.35)

    # Mock is_available 返回 False，防止调用真实 CLI
    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available",
        lambda self: False,
    )
    result = process_pending_batches("claude")
    assert result == 0


def test_process_pending_batches_no_pending(monkeypatch, tmp_path):
    """队列为空时正常返回（不调用真实 CLI）"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()

    monkeypatch.setattr(
        "app.ai.cli_client.ClaudeCodeCLI.is_available",
        lambda self: False,
    )
    result = process_pending_batches("claude")
    assert result == 0


def test_process_pending_batches_passes_candidate_labels(monkeypatch, tmp_path):
    """process_pending_batches 应向 backend 传递收集到的候选标签"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_data(conn)
        conn.execute(
            """INSERT INTO classification_rules
               (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
               VALUES ('dim6', 'material', '钢筋', 'keyword', 1, 0.6, 1)"""
        )
        clause_ids = [r["id"] for r in conn.execute("SELECT id FROM clauses LIMIT 20")]
    for cid in clause_ids:
        add_to_queue(cid, "dim6", 0.35)

    captured = {}

    class FakeBackend:
        def is_available(self):
            return True

        def classify_batch_sync(self, batch, dim, candidate_labels=None):
            captured["dim"] = dim
            captured["labels"] = candidate_labels
            return []

    monkeypatch.setattr("app.ai.classifier_ai.get_backend", lambda name: FakeBackend())
    result = process_pending_batches("claude")
    assert result == 0
    assert captured["dim"] == "dim6"
    assert "钢筋" in captured["labels"]
