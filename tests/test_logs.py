"""system_logs 埋点与日志管理测试（spec §9 规划 tests/test_logs.py）

P3 日志管理：随 Task 递增覆盖 auth/import/spec/rule/review/classify 埋点、
90 天保留清理、日志列表/导出/手动清理，QA 日志只读见 tests/test_logs_ui.py。
"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path, name="logs.db"):
    """独立临时库（auth_client 的 cookie 凭 JWT 签名跨库有效，可复用于新库）"""
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / f"lance-{name}"))
    init_db()
    return db_path


def _insert_admin():
    from app.auth import hash_password
    with get_db() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("admin", hash_password("test123")))


def _logs(action=None, category=None):
    """查询 system_logs，返回 dict 列表（可按 action/category 过滤）"""
    with get_db() as conn:
        sql = "SELECT * FROM system_logs"
        conds, params = [], []
        if action:
            conds.append("action = ?")
            params.append(action)
        if category:
            conds.append("category = ?")
            params.append(category)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------- auth 埋点 ----------

def test_login_success_logs_auth_info(auth_client):
    """登录成功 → system_logs 记录 auth/INFO/登录成功，username=admin"""
    rows = _logs(action="登录成功", category="auth")
    assert len(rows) == 1
    assert rows[0]["level"] == "INFO"
    assert rows[0]["username"] == "admin"


def test_login_failure_logs_auth_warn(client, monkeypatch, tmp_path):
    """错误密码 → auth/WARN/登录失败，记录尝试的用户名"""
    _setup(monkeypatch, tmp_path)
    _insert_admin()
    resp = client.post("/login", data={"username": "admin", "password": "wrong"},
                       follow_redirects=False)
    assert resp.status_code == 200  # 重新渲染登录页
    rows = _logs(action="登录失败", category="auth")
    assert len(rows) == 1
    assert rows[0]["level"] == "WARN"
    assert rows[0]["username"] == "admin"


def test_logout_logs_auth_info(auth_client, monkeypatch, tmp_path):
    """登出 → auth/INFO/登出，username 取中间件写入的 request.state.username"""
    _setup(monkeypatch, tmp_path)
    resp = auth_client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
    rows = _logs(action="登出", category="auth")
    assert len(rows) == 1
    assert rows[0]["username"] == "admin"
