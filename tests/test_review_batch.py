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


def test_batch_confirm_filters_non_review_items(auth_client, monkeypatch, tmp_path):
    """queue 中混入 status='done' 的 id，批量 confirm 后 done 项不被二次处理（不重复累加 confirmed/hit）"""
    db_path = tmp_path / "rb4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
        clause = conn.execute("SELECT id FROM clauses LIMIT 1").fetchone()
        rid = conn.execute(
            """INSERT INTO classification_queue (clause_id, dimension, status, ai_label, ai_confidence)
               VALUES (?, 'dim4', 'review', '结构专业', 0.9)""",
            (clause["id"],),
        ).lastrowid
        did = conn.execute(
            """INSERT INTO classification_queue (clause_id, dimension, status, ai_label, ai_confidence)
               VALUES (?, 'dim4', 'done', '结构专业', 0.9)""",
            (clause["id"],),
        ).lastrowid

    # 记录 process_feedback 调用：done 项被过滤后应只处理 review 项一次
    calls = []
    import app.classifier.feedback as fb
    real = fb.process_feedback

    def spy(clause_id, dimension, confirmed_label, source_conf=0.0):
        calls.append(clause_id)
        real(clause_id, dimension, confirmed_label, source_conf)

    monkeypatch.setattr(fb, "process_feedback", spy)

    resp = auth_client.post("/review/batch-confirm", json={"queue_ids": [rid, did]})
    assert resp.status_code == 200
    # done 项不应被二次处理，仅 review 项走反馈闭环
    assert calls == [clause["id"]]
    with get_db() as conn:
        done = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE id IN (?,?) AND status='done'",
            (rid, did),
        ).fetchone()[0]
    assert done == 2
