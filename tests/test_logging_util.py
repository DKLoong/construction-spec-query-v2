"""log_action 日志工具测试"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "log.db"))
    init_db()


def test_log_action_writes_system_logs(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.logging_util import log_action
    log_action("maintenance", "INFO", "健康检查", detail='{"checks":1}', username="admin")
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM system_logs WHERE action = '健康检查'"
        ).fetchone()
    assert row is not None
    assert row["category"] == "maintenance"
    assert row["level"] == "INFO"
    assert row["detail"] == '{"checks":1}'
    assert row["username"] == "admin"


def test_log_action_level_warn(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.logging_util import log_action
    log_action("spec", "WARN", "孤立条文", detail="2")
    with get_db() as conn:
        row = conn.execute(
            "SELECT level FROM system_logs WHERE action = '孤立条文'"
        ).fetchone()
    assert row["level"] == "WARN"
