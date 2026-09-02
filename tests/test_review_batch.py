"""复核队列批量确认/驳回测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _seed_queue(conn, n=2):
    setup_search_data(conn)
    clause = conn.execute("SELECT id, content FROM clauses LIMIT 1").fetchone()
    ids = []
    for i in range(n):
        cur = conn.execute(
            """INSERT INTO classification_queue (clause_id, dimension, status, ai_label, ai_confidence)
               VALUES (?, 'dim4', 'review', ?, ?)""",
            (clause["id"], f"结构专业{i}", 0.8 + i * 0.05),
        )
        ids.append(cur.lastrowid)
    return ids


def test_batch_confirm_updates_queue(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rb1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        ids = _seed_queue(conn)
    resp = auth_client.post("/review/batch-confirm", json={"queue_ids": ids})
    assert resp.status_code == 200
    with get_db() as conn:
        done = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE id IN (?,?) AND status='done'",
            (ids[0], ids[1]),
        ).fetchone()[0]
    assert done == 2


def test_batch_reject_updates_queue(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rb2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        ids = _seed_queue(conn)
    resp = auth_client.post("/review/batch-reject", json={"queue_ids": ids})
    assert resp.status_code == 200
    with get_db() as conn:
        rej = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE id IN (?,?) AND status='rejected'",
            (ids[0], ids[1]),
        ).fetchone()[0]
    assert rej == 2


def test_batch_empty_ids_noop(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rb3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    resp = auth_client.post("/review/batch-confirm", json={"queue_ids": []})
    assert resp.status_code == 200
