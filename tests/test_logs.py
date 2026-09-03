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


# ---------- import 埋点 ----------

class _FakeBackend:
    """validate_version 测试用假 AI 后端（get_backend 返回对象接口）"""

    def __init__(self, available=True, content="", success=True):
        self._available = available
        self._content = content
        self._success = success

    def is_available(self):
        return self._available

    async def ask(self, prompt="", context="", system_prompt="", work_dir=""):
        return type("Resp", (), {"success": self._success, "content": self._content})()


_OK_JSON = ('{"status": "现行", "replaced_by_code": "", '
            '"corrected_code": "GB/T 1-2020", "corrected_title": "测试规范"}')


def test_validate_version_success_logs_info(auth_client, monkeypatch, tmp_path):
    """AI 可用且返回 JSON → import/INFO/版本校验完成"""
    import app.ai.cli_client as cc
    monkeypatch.setattr(cc, "get_backend", lambda: _FakeBackend(content=_OK_JSON))
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/import/validate-version",
                            json={"code": "GBT 1-2020", "title": "测试规范"})
    assert resp.status_code == 200 and resp.json().get("ai_available") is True
    rows = _logs(action="版本校验完成", category="import")
    assert len(rows) == 1
    assert rows[0]["level"] == "INFO"
    assert rows[0]["username"] == "admin"


def test_validate_version_ai_unavailable_logs_warn(auth_client, monkeypatch, tmp_path):
    """AI 不可用 → import/WARN/版本校验降级(AI不可用)，不出现 INFO 行"""
    import app.ai.cli_client as cc
    monkeypatch.setattr(cc, "get_backend", lambda: _FakeBackend(available=False))
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/import/validate-version",
                            json={"code": "GB/T 1-2020", "title": "测试规范"})
    assert resp.status_code == 200 and resp.json().get("ai_available") is False
    assert len(_logs(action="版本校验完成", category="import")) == 0
    rows = _logs(action="版本校验降级(AI不可用)", category="import")
    assert len(rows) == 1 and rows[0]["level"] == "WARN"


def test_upload_duplicate_file_logs_warn(auth_client, monkeypatch, tmp_path):
    """重复文件命中 → import/WARN/导入重复文件"""
    import hashlib
    _setup(monkeypatch, tmp_path)
    content = b"dup-markdown-content"
    fh = hashlib.sha256(content).hexdigest()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, file_hash, status) VALUES (?, ?, ?, '现行')",
            ("GB/T 1-2010", "已导入规范", fh))
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("dup.md", content, "text/markdown")},
        data={"title": "新文件", "code": "GB/T 2-2020", "status": "现行"})
    assert resp.status_code == 200 and "已导入过" in resp.text
    rows = _logs(action="导入重复文件", category="import")
    assert len(rows) == 1 and rows[0]["level"] == "WARN"


def test_confirm_review_logs_info(auth_client, monkeypatch, tmp_path):
    """审查确认继续导入 → import/INFO，username=任务属主"""
    import app.routes.import_routes as ir
    _setup(monkeypatch, tmp_path)
    tid = "aa000011"
    ir.progress_store[tid] = {
        "status": "review_needed", "progress": 50, "owner": "admin",
        "md_text": "# x", "title": "测试规范", "code": "GB/T 1-2010",
        "file_path": "x.md", "file_hash": "", "spec_status": "现行",
        "replaced_by_code": "",
    }
    monkeypatch.setattr(ir, "_process_import_phase2", lambda *a, **k: None)
    try:
        resp = auth_client.post(f"/import/review/{tid}/confirm", data={"content": "# x"})
        assert resp.status_code == 200
        rows = _logs(action="审查确认继续导入", category="import")
        assert len(rows) == 1 and rows[0]["username"] == "admin"
    finally:
        ir.progress_store.pop(tid, None)


def test_cancel_review_logs_info(auth_client, monkeypatch, tmp_path):
    """取消审查 → import/INFO/取消导入审查，username=任务属主"""
    import app.routes.import_routes as ir
    _setup(monkeypatch, tmp_path)
    tid = "bb000022"
    ir.progress_store[tid] = {"status": "review_needed", "owner": "admin"}
    try:
        resp = auth_client.post(f"/import/review/{tid}/cancel")
        assert resp.status_code == 200
        rows = _logs(action="取消导入审查", category="import")
        assert len(rows) == 1 and rows[0]["username"] == "admin"
    finally:
        ir.progress_store.pop(tid, None)


def test_import_phase2_success_logs_info(monkeypatch, tmp_path):
    """Phase 2 导入成功（commit 后）→ import/INFO/导入成功，detail 含条文数"""
    import app.routes.import_routes as ir
    _setup(monkeypatch, tmp_path)

    class _NoStore:
        def __init__(self):
            raise RuntimeError("skip vector store")

    monkeypatch.setattr(ir, "VectorStore", _NoStore)
    monkeypatch.setattr(ir, "parse_markdown", lambda text: [
        {"clause_no": "3.1.1", "title": "检验", "content": "进场钢筋应检验。",
         "is_non_clause": False, "parent_path": []}])
    tid = "cc000033"
    ir.progress_store[tid] = {"owner": "admin"}
    try:
        ir._process_import_phase2(tid, "# GB", "测试规范", "GB/T 50000-2010",
                                  "none.md", "", "现行", "")
        rows = _logs(action="导入成功", category="import")
        assert len(rows) == 1
        assert rows[0]["level"] == "INFO"
        assert '"clause_count": 1' in rows[0]["detail"]
    finally:
        ir.progress_store.pop(tid, None)


def test_import_phase2_failure_logs_error(monkeypatch, tmp_path):
    """Phase 2 异常（rollback 后）→ import/ERROR/导入失败，detail 含异常信息"""
    import app.routes.import_routes as ir
    _setup(monkeypatch, tmp_path)

    def _boom(text):
        raise RuntimeError("boom-解析异常")

    monkeypatch.setattr(ir, "parse_markdown", _boom)
    tid = "dd000044"
    ir.progress_store[tid] = {"owner": "system"}
    try:
        ir._process_import_phase2(tid, "", "", "", "none.md")
        rows = _logs(action="导入失败", category="import")
        assert len(rows) == 1
        assert rows[0]["level"] == "ERROR"
        assert "boom" in rows[0]["detail"]
    finally:
        ir.progress_store.pop(tid, None)
