"""健康检查埋点的操作者：自动(load, trigger=auto)→system；手动按钮→当前登录用户"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "hc_operator.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()


def _last_health_log():
    with get_db() as conn:
        row = conn.execute(
            "SELECT username, action FROM system_logs "
            "WHERE action = '健康检查' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def test_auto_health_check_logs_system(auth_client, monkeypatch, tmp_path):
    """进入页面 #health-result 的 load（带 trigger=auto）→ 操作者记 system"""
    _setup(monkeypatch, tmp_path)
    auth_client.post("/maintenance/health-check", data={"trigger": "auto"})
    row = _last_health_log()
    assert row is not None and row["username"] == "system"


def test_manual_health_check_logs_admin(auth_client, monkeypatch, tmp_path):
    """手动点「运行健康检查」（不带 trigger）→ 操作者记当前登录用户 admin"""
    _setup(monkeypatch, tmp_path)
    auth_client.post("/maintenance/health-check", data={})
    row = _last_health_log()
    assert row is not None and row["username"] == "admin"
