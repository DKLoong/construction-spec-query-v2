import pytest
from app.database import init_db, get_db
from app.classifier import batch_queue
from app.termdict import invalidate_term_cache


def _db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(p))
    init_db()


def _seed(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 1', '规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, '5.1', 'x', '含钢筋的条文')",
        (spec_id,))
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return cid


def _enqueue(conn, clause_id, dimension="dim6"):
    batch_queue.try_enqueue(conn, clause_id, dimension, 0.0)
    conn.execute(
        "UPDATE classification_queue SET batch_id='b1', status='ai_processing' "
        "WHERE clause_id=? AND dimension=?", (clause_id, dimension))


def test_ai_high_conf_invalid_label_downgraded_to_review(monkeypatch, tmp_path):
    """≥0.7 但 label 不在词典 → review：不写列、不沉淀规则。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source) VALUES ('dim6','钢筋','钢筋','manual')")
        cid = _seed(conn)
        _enqueue(conn, cid)
    invalidate_term_cache()
    batch_queue.apply_ai_results("b1", [{"clause_id": cid, "label": "style", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert q["status"] == "review"
    assert c["dim6_material"] is None
    assert rules == 0


def test_ai_high_conf_valid_label_auto_adopts(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source) VALUES ('dim6','钢筋','钢筋','manual')")
        cid = _seed(conn)
        _enqueue(conn, cid)
    invalidate_term_cache()
    batch_queue.apply_ai_results("b1", [{"clause_id": cid, "label": "钢筋", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
    assert q["status"] == "auto_adopted"
    assert c["dim6_material"] == "钢筋"
