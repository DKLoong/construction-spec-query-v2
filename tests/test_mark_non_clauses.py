"""存量打标脚本（scripts/mark_non_clauses.py）测试"""
from app.database import init_db, get_db
from scripts.mark_non_clauses import mark_non_clauses


def test_mark_non_clauses_marks_and_idempotent(monkeypatch, tmp_path):
    """存量脚本：标题命中黑名单的条文打标 clause_is_non=1，且幂等可重跑"""
    db_path = tmp_path / "test_mark.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "正常条文", 0),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "编制说明", 0),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "", "条文说明", "条文说明内容", 0),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "", "目次", "目次内容", 0),
        )

        # 首次执行：前言/条文说明/目次 命中黑名单 → 打标
        n, ids = mark_non_clauses(conn)
        assert n == 3

        # 幂等：重复执行不产生额外更新
        n2, ids2 = mark_non_clauses(conn)
        assert n2 == 3

        # 校验库中标记：正常条文保持 0，其余均为 1
        rows = conn.execute(
            "SELECT clause_no, title, clause_is_non FROM clauses ORDER BY id"
        ).fetchall()
        assert [dict(r)["clause_is_non"] for r in rows] == [0, 1, 1, 1]
        # 不删除任何行（目次/Contents 仅打标，不删除）
        total = conn.execute("SELECT COUNT(*) as c FROM clauses").fetchone()["c"]
        assert total == 4


def test_mark_non_clauses_normal_title_not_marked(monkeypatch, tmp_path):
    """正常条文标题不应被打标"""
    db_path = tmp_path / "test_mark_normal.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "5.1.1", "模板设计", "模板及支架设计", 0),
        )
        n, _ = mark_non_clauses(conn)
        assert n == 0
