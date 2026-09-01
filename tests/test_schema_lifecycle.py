"""生命周期相关 schema 迁移测试（幂等 + 可读写）"""
from app.database import init_db, get_db


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_schema_migration_adds_lifecycle_columns(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()
    with get_db() as conn:
        cols = _columns(conn, "specifications")
        assert "replace_by_spec_id" in cols
        assert "spec_version" in cols


def test_schema_migration_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t2.db"))
    init_db()
    init_db()  # 二次执行不报错
    with get_db() as conn:
        cols = _columns(conn, "specifications")
        assert "replace_by_spec_id" in cols


def test_schema_creates_system_logs_table(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t3.db"))
    init_db()
    with get_db() as conn:
        cols = _columns(conn, "system_logs")
        for c in ("category", "level", "action", "detail", "username",
                  "duration_ms", "created_at"):
            assert c in cols


def test_schema_system_logs_writable(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t4.db"))
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO system_logs (category, level, action, username) VALUES (?,?,?,?)",
            ("spec", "INFO", "测试动作", "tester"),
        )
        row = conn.execute(
            "SELECT * FROM system_logs WHERE action = '测试动作'"
        ).fetchone()
        assert row["category"] == "spec" and row["level"] == "INFO"


def test_schema_creates_health_snapshot_table(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t5.db"))
    init_db()
    with get_db() as conn:
        cols = _columns(conn, "health_check_snapshots")
        assert "result" in cols and "created_at" in cols
