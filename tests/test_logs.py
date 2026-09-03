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


# ---------- spec / clause 埋点 ----------

def _seed_spec_clause(with_clause=True):
    """插入一条规范（可选含一条未分类条文），返回 (spec_id, clause_id|None)"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES ('GB/T 1-2020', '测试规范', '现行')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        cid = None
        if with_clause:
            conn.execute(
                """INSERT INTO clauses (spec_id, clause_no, title, content, search_text,
                   dim4_specialty, dim5_location, dim6_material, ai_classified, needs_review)
                   VALUES (?, '3.1.1', '钢筋', '进场应检验。', '', '', '', '', 0, 0)""",
                (sid,))
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return sid, cid


def test_update_spec_status_logs_spec_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, _ = _seed_spec_clause(with_clause=False)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "废止"})
    assert resp.status_code == 200
    rows = _logs(action="修改规范状态", category="spec")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"old": "现行"' in rows[0]["detail"] and '"new": "废止"' in rows[0]["detail"]


def test_delete_spec_logs_spec_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, _ = _seed_spec_clause()
    resp = auth_client.delete(f"/specs/{sid}")
    assert resp.status_code == 200
    rows = _logs(action="删除规范", category="spec")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"clause_count": 1' in rows[0]["detail"]


def test_update_clause_logs_spec_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, cid = _seed_spec_clause()
    resp = auth_client.put(f"/specs/{sid}/clauses/{cid}",
                           data={"clause_no": "4.1.1", "title": "更新", "content": "新内容"})
    assert resp.status_code == 200
    rows = _logs(action="编辑条文", category="spec")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"


def test_delete_clause_logs_spec_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, cid = _seed_spec_clause()
    resp = auth_client.delete(f"/specs/{sid}/clauses/{cid}")
    assert resp.status_code == 200
    rows = _logs(action="删除条文", category="spec")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"


def test_update_spec_class_logs_spec_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, _ = _seed_spec_clause(with_clause=False)
    resp = auth_client.put(f"/specs/{sid}/class",
                           data={"dim2_stage": "施工", "dim3_usage": "验收"})
    assert resp.status_code == 200
    rows = _logs(action="修改规范分类", category="spec")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"


def test_update_clause_class_logs_spec_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, cid = _seed_spec_clause()
    resp = auth_client.put(f"/specs/{sid}/clauses/{cid}/class",
                           data={"dim4_specialty": "结构专业", "dim5_location": "主体"})
    assert resp.status_code == 200
    rows = _logs(action="修改条文分类", category="spec")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"


def test_batch_reclassify_logs_classify(auth_client, monkeypatch, tmp_path):
    """批量重分类归属 classify 类；AI 完成正常 → INFO"""
    import app.ai.classifier_ai as ca
    _setup(monkeypatch, tmp_path)
    sid, _ = _seed_spec_clause(with_clause=True)
    monkeypatch.setattr(ca, "process_pending_batches", lambda **k: None)
    resp = auth_client.post("/specs/batch-reclassify",
                            json={"spec_ids": [sid], "scope": "unclassified"})
    assert resp.status_code == 200
    rows = _logs(action="批量重分类", category="classify")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"target_count": 1' in rows[0]["detail"]


def test_batch_reclassify_ai_failure_logs_warn(auth_client, monkeypatch, tmp_path):
    """入队后 AI 抛异常 → classify/WARN；入队动作本身仍记 INFO"""
    import app.ai.classifier_ai as ca
    _setup(monkeypatch, tmp_path)
    sid, _ = _seed_spec_clause(with_clause=True)

    def _boom(**k):
        raise RuntimeError("ai-boom")

    monkeypatch.setattr(ca, "process_pending_batches", _boom)
    resp = auth_client.post("/specs/batch-reclassify",
                            json={"spec_ids": [sid], "scope": "unclassified"})
    assert resp.status_code == 200 and "AI 分类未完成" in resp.text
    rows = _logs(action="批量重分类已入队但AI未完成", category="classify")
    assert len(rows) == 1 and rows[0]["level"] == "WARN"
    assert "ai-boom" in rows[0]["detail"]
    assert len(_logs(action="批量重分类", category="classify")) == 1


# ---------- rule 埋点 ----------

def _seed_rule(pattern="钢筋", is_active=1, hit_count=0, confirmed=0,
               locked=None, created_at_old=False):
    """插入一条规则，返回 rule_id"""
    with get_db() as conn:
        if created_at_old:
            conn.execute(
                """INSERT INTO classification_rules
                   (dimension, sub_field, pattern, match_type, priority, threshold,
                    is_active, hit_count, confirmed, locked, created_at)
                   VALUES ('dim4', 't', ?, 'keyword', 0, 0.6, ?, ?, ?, ?, datetime('now','localtime','-40 days'))""",
                (pattern, is_active, hit_count, confirmed, locked))
        else:
            conn.execute(
                """INSERT INTO classification_rules
                   (dimension, sub_field, pattern, match_type, priority, threshold,
                    is_active, hit_count, confirmed, locked)
                   VALUES ('dim4', 't', ?, 'keyword', 0, 0.6, ?, ?, ?, ?)""",
                (pattern, is_active, hit_count, confirmed, locked))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_create_rule_logs_rule_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/rules/create", data={
        "dimension": "dim4", "sub_field": "梁", "pattern": "梁柱节点",
        "match_type": "keyword"})
    assert resp.status_code == 200
    rows = _logs(action="新建规则", category="rule")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert "梁柱节点" in rows[0]["detail"]


def test_toggle_rule_logs_rule_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    rid = _seed_rule(is_active=1)
    resp = auth_client.post(f"/rules/{rid}/toggle")
    assert resp.status_code == 200
    rows = _logs(action="停用规则", category="rule")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"is_active": 0' in rows[0]["detail"]


def test_toggle_rule_lock_logs_rule_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    rid = _seed_rule()
    resp = auth_client.post(f"/rules/{rid}/lock")
    assert resp.status_code == 200
    rows = _logs(action="锁定规则", category="rule")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"locked": 1' in rows[0]["detail"]


def test_delete_rule_logs_rule_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    rid = _seed_rule()
    resp = auth_client.delete(f"/rules/{rid}")
    assert resp.status_code == 200
    rows = _logs(action="删除规则", category="rule")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"


def test_update_rule_logs_rule_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    rid = _seed_rule()
    resp = auth_client.put(f"/rules/{rid}",
                           data={"dimension": "dim5", "pattern": "外墙保温"})
    assert resp.status_code == 200
    rows = _logs(action="编辑规则", category="rule")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert "外墙保温" in rows[0]["detail"]


def test_quality_batch_logs_rule_info(auth_client, monkeypatch, tmp_path):
    """一键删除僵尸规则 → rule/INFO/规则质量批量处理，detail 含 affected"""
    _setup(monkeypatch, tmp_path)
    _seed_rule(pattern="僵尸规则", hit_count=0, confirmed=0,
               locked=None, created_at_old=True)
    resp = auth_client.post("/rules/quality/batch",
                            data={"action": "delete_all", "kind": "zombie"})
    assert resp.status_code == 200
    rows = _logs(action="规则质量批量处理", category="rule")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"affected": 1' in rows[0]["detail"]


# ---------- review / classify 埋点 ----------

def _seed_review_queue(clause_id, label="钢筋", conf=0.6):
    """插入一条 status='review' 队列项，返回 queue_id"""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_queue
               (clause_id, dimension, keyword_score, ai_label, ai_confidence, status)
               VALUES (?, 'dim6', 0.0, ?, ?, 'review')""",
            (clause_id, label, conf))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_confirm_review_logs_review_info(auth_client, monkeypatch, tmp_path):
    import app.classifier.feedback as fb
    _setup(monkeypatch, tmp_path)
    sid, cid = _seed_spec_clause()
    qid = _seed_review_queue(cid, label="钢筋")
    monkeypatch.setattr(fb, "process_feedback", lambda *a, **k: None)
    resp = auth_client.post(f"/review/{qid}/confirm")
    assert resp.status_code == 200
    rows = _logs(action="确认分类标签", category="review")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert "钢筋" in rows[0]["detail"]


def test_confirm_review_missing_logs_warn(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/review/999999/confirm")
    assert resp.status_code == 200
    rows = _logs(action="确认失败-队列项不存在", category="review")
    assert len(rows) == 1 and rows[0]["level"] == "WARN"


def test_reject_review_logs_review_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, cid = _seed_spec_clause()
    qid = _seed_review_queue(cid)
    resp = auth_client.post(f"/review/{qid}/reject")
    assert resp.status_code == 200
    rows = _logs(action="驳回分类标签", category="review")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"


def test_batch_confirm_logs_review_info(auth_client, monkeypatch, tmp_path):
    import app.classifier.feedback as fb
    _setup(monkeypatch, tmp_path)
    sid, cid = _seed_spec_clause()
    q1, q2 = _seed_review_queue(cid), _seed_review_queue(cid)
    monkeypatch.setattr(fb, "process_feedback", lambda *a, **k: None)
    resp = auth_client.post("/review/batch-confirm", json={"queue_ids": [q1, q2]})
    assert resp.status_code == 200
    rows = _logs(action="批量确认", category="review")
    assert len(rows) == 1 and '"count": 2' in rows[0]["detail"]


def test_batch_reject_logs_review_info(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sid, cid = _seed_spec_clause()
    q1, q2 = _seed_review_queue(cid), _seed_review_queue(cid)
    resp = auth_client.post("/review/batch-reject", json={"queue_ids": [q1, q2]})
    assert resp.status_code == 200
    rows = _logs(action="批量驳回", category="review")
    assert len(rows) == 1 and '"count": 2' in rows[0]["detail"]


def test_run_classifier_logs_classify_info(auth_client, monkeypatch, tmp_path):
    import app.ai.classifier_ai as ca
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(ca, "process_pending_batches", lambda **k: 3)
    resp = auth_client.post("/classify/run")
    assert resp.status_code == 200
    rows = _logs(action="运行AI分类", category="classify")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"count": 3' in rows[0]["detail"]


def test_run_classifier_failure_logs_classify_error(auth_client, monkeypatch, tmp_path):
    import app.ai.classifier_ai as ca
    _setup(monkeypatch, tmp_path)

    def _boom(**k):
        raise RuntimeError("classify-boom")

    monkeypatch.setattr(ca, "process_pending_batches", _boom)
    resp = auth_client.post("/classify/run")
    assert resp.status_code == 200
    rows = _logs(action="运行AI分类失败", category="classify")
    assert len(rows) == 1 and rows[0]["level"] == "ERROR"
    assert "classify-boom" in rows[0]["detail"]


def test_apply_ai_results_logs_classify(monkeypatch, tmp_path):
    """apply_ai_results 每批记一条 classify/INFO，detail 含 auto/review 计数"""
    from app.classifier import batch_queue
    _setup(monkeypatch, tmp_path)
    results = [
        {"clause_id": 101, "confidence": 0.85, "label": "钢筋"},
        {"clause_id": 102, "confidence": 0.50, "label": "混凝土"},
    ]
    batch_queue.apply_ai_results("batch_t1", results)
    rows = _logs(action="AI分类结果入库", category="classify")
    assert len(rows) == 1 and rows[0]["level"] == "INFO"
    assert '"total": 2' in rows[0]["detail"]
    assert '"auto_adopted": 1' in rows[0]["detail"]
    assert '"review": 1' in rows[0]["detail"]
