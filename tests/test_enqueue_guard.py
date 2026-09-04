# -*- coding: utf-8 -*-
"""入列自检守卫测试（#4）：add_to_queue 幂等 + 过滤废止规范/非条文/已删除条文"""
from app.classifier.batch_queue import add_to_queue
from app.database import init_db, get_db


def _db(monkeypatch, tmp_path, name="guard.db"):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    return db_path


def _seed(conn, spec_status="现行", non_clause=0):
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES ('GB 50204', '测试规范', ?)",
        (spec_status,),
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO clauses (spec_id, clause_no, content, clause_is_non)
           VALUES (?, '1.0.1', '混凝土 模板 钢筋 施工', ?)""",
        (sid, non_clause),
    )
    return conn.execute("SELECT id FROM clauses ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _qcount(conn, clause_id=None, dimension="dim6"):
    if clause_id is None:
        return conn.execute("SELECT COUNT(*) AS n FROM classification_queue").fetchone()["n"]
    return conn.execute(
        "SELECT COUNT(*) AS n FROM classification_queue WHERE clause_id = ? AND dimension = ?",
        (clause_id, dimension),
    ).fetchone()["n"]


def test_add_success_returns_true(monkeypatch, tmp_path):
    """正常入列返回 True 且仅一行"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed(conn)
    assert add_to_queue(cid, "dim6", 0.35) is True
    with get_db() as conn:
        assert _qcount(conn, cid) == 1


def test_duplicate_pending_skipped(monkeypatch, tmp_path):
    """同一 (clause,dimension) 已有 pending → 跳过（幂等）"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed(conn)
    assert add_to_queue(cid, "dim6", 0.35) is True
    assert add_to_queue(cid, "dim6", 0.35) is False
    with get_db() as conn:
        assert _qcount(conn, cid) == 1


def test_ai_processing_in_flight_skipped(monkeypatch, tmp_path):
    """已有 ai_processing（取批在跑）→ 跳过，避免重复取批"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed(conn)
    add_to_queue(cid, "dim6", 0.35)
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_queue SET status='ai_processing' WHERE clause_id=? AND dimension='dim6'",
            (cid,),
        )
    assert add_to_queue(cid, "dim6", 0.35) is False
    with get_db() as conn:
        assert _qcount(conn, cid) == 1


def test_settled_status_also_skipped(monkeypatch, tmp_path):
    """同 (clause,dimension) 已到终态(auto_adopted/review) 也防重，避免队列重复膨胀"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed(conn)
    add_to_queue(cid, "dim6", 0.35)
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_queue SET status='auto_adopted' WHERE clause_id=? AND dimension='dim6'",
            (cid,),
        )
    assert add_to_queue(cid, "dim6", 0.35) is False
    with get_db() as conn:
        assert _qcount(conn, cid) == 1


def test_clear_then_requeue_allowed(monkeypatch, tmp_path):
    """先清旧队列项再入列（spec 重跑路径）仍成功"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed(conn)
    add_to_queue(cid, "dim6", 0.35)
    with get_db() as conn:
        conn.execute("DELETE FROM classification_queue WHERE clause_id = ?", (cid,))
    assert add_to_queue(cid, "dim6", 0.35) is True
    with get_db() as conn:
        assert _qcount(conn, cid) == 1


def test_obsolete_spec_skipped(monkeypatch, tmp_path):
    """废止规范条文不入 AI 队列"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed(conn, spec_status="废止")
    assert add_to_queue(cid, "dim6", 0.35) is False
    with get_db() as conn:
        assert _qcount(conn) == 0


def test_non_clause_skipped(monkeypatch, tmp_path):
    """非条文（前言/条文说明 clause_is_non=1）不入 AI 队列"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed(conn, non_clause=1)
    assert add_to_queue(cid, "dim6", 0.35) is False
    with get_db() as conn:
        assert _qcount(conn) == 0


def test_deleted_clause_skipped(monkeypatch, tmp_path):
    """条文已删除（不存在）→ 跳过"""
    _db(monkeypatch, tmp_path)
    assert add_to_queue(999999, "dim6", 0.35) is False
    with get_db() as conn:
        assert _qcount(conn) == 0
