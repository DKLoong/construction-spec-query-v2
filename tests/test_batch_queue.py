from app.classifier.batch_queue import add_to_queue, get_pending_batch, apply_ai_results
from app.classifier.feedback import extract_keywords, process_feedback
from app.database import init_db, get_db


def setup_sample_data(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '测试规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i in range(25):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
            (spec_id, f"1.0.{i+1}", f"这是第{i+1}条测试条文 混凝土施工"),
        )
    return spec_id


def test_add_to_queue(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        spec_id = setup_sample_data(conn)
        clause_id = conn.execute("SELECT id FROM clauses LIMIT 1").fetchone()["id"]
    add_to_queue(clause_id, "dim6", 0.35)
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE clause_id = ?",
            (clause_id,)
        ).fetchone()[0]
        assert count == 1


def test_get_pending_batch(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        spec_id = setup_sample_data(conn)
        clause_ids = [r["id"] for r in conn.execute("SELECT id FROM clauses LIMIT 20")]
    for cid in clause_ids:
        add_to_queue(cid, "dim6", 0.35)
    batch = get_pending_batch("dim6")
    assert len(batch) >= 1


def test_apply_ai_results(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        spec_id = setup_sample_data(conn)
        clause_ids = [r["id"] for r in conn.execute("SELECT id FROM clauses LIMIT 20")]
    for cid in clause_ids:
        add_to_queue(cid, "dim6", 0.35)
    batch = get_pending_batch("dim6")
    assert len(batch) > 0
    batch_id = batch[0].get("batch_id")
    target_clause = clause_ids[0]
    if batch_id:
        apply_ai_results(batch_id, [
            {"clause_id": target_clause, "label": "混凝土材料", "confidence": 0.85},
        ])
        with get_db() as conn:
            status = conn.execute(
                "SELECT status FROM classification_queue WHERE clause_id = ?",
                (target_clause,)
            ).fetchone()["status"]
            assert status == "auto_adopted"


def test_extract_keywords():
    text = "模板及其支架应根据工程结构形式进行设计。模板的接缝不应漏浆。"
    keywords = extract_keywords(text, top_n=5)
    assert len(keywords) >= 1
    assert any("模板" in kw for kw in keywords)
