import sqlite3
from app.database import get_connection, get_db, init_db, SCHEMA_SQL


def test_get_connection_returns_sqlite_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    conn = get_connection()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_get_db_context_manager(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    with get_db() as conn:
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM test").fetchone()
        assert row["id"] == 1


def test_init_db_creates_all_tables(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "specifications" in names
        assert "clauses" in names
        assert "classification_rules" in names
        assert "classification_queue" in names
        assert "users" in names


def test_init_db_creates_fts5(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [t["name"] for t in tables]
        fts_tables = [n for n in names if "fts" in n.lower()]
        assert len(fts_tables) >= 1


def test_init_db_is_idempotent(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    init_db()  # 不应报错


def test_clauses_table_has_clause_is_non_column(monkeypatch, tmp_path):
    """clauses 表应有 clause_is_non 列，且默认 0（存量 INSERT 未列新列也可兜底）"""
    db_path = tmp_path / "test_noncol.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(clauses)")]
        assert "clause_is_non" in cols
        # 存量 INSERT（未显式给出 clause_is_non）默认落库为 0
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-T', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "内容"),
        )
        row = conn.execute("SELECT clause_is_non FROM clauses WHERE clause_no='1.0.1'").fetchone()
        assert row["clause_is_non"] == 0


def test_synonym_map_table_exists(monkeypatch, tmp_path):
    """init_db 应创建 synonym_map 表"""
    db_path = tmp_path / "test_syn_table.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "synonym_map" in names


def test_synonym_map_seed_idempotent(monkeypatch, tmp_path):
    """init_db 预置常用同义词且幂等（重复初始化不产生重复行）"""
    db_path = tmp_path / "test_syn_seed.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    init_db()  # 再次初始化，验证幂等
    with get_db() as conn:
        rows = conn.execute(
            "SELECT source, target, is_active FROM synonym_map ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        sources = {r["source"] for r in rows}
        assert sources == {"砼", "箍筋"}
        assert all(r["is_active"] == 1 for r in rows)
