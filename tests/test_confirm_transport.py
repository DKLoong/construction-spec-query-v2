"""审查确认接口的传输契约

背景（2026-09-26 实测）：确认接口原先把整份 markdown 当作**单个表单字段**回传，
撞上 Starlette `MultiPartParser.max_part_size = 1048576` 硬上限（**urlencoded 同样
受管**）→ FastAPI 返回 HTTP 400 `Field exceeded maximum size of 1024KB.`，路由体
根本不执行（故 system_logs 里成功/失败/确认三条都没有）。而 HTMX 对 4xx 默认不
swap，页面上什么都不发生——观感就是「点了确认，等半天没反应」。

实测边界：JGJ 107-2016 的 md 57,645 字符（编码后 0.15MB）通过；CJJ 2-2008 的
md 460,011 字符（编码后 1.52MB）失败。编码比例约 3.46 字节/字符，安全线约
md 30 万字符——即「大规范根本导不进来」。

本文件锁定：md 走 **raw body**（无字段大小上限），同时保留原有守卫语义。
"""
from app.routes.import_routes import progress_store

# 单字段上限 1MB；取 1.5MB 复刻 CJJ 2-2008 的实测失败尺寸
_OVER_LIMIT_BYTES = 1_500_000


def _seed_task(task_id, **overrides):
    """在 progress_store 里放一个待审查任务；默认状态为 review_needed"""
    task = {
        "status": "review_needed",
        "progress": 50,
        "message": "OCR 完成，请审查识别结果",
        "md_text": "# 测试规范\n## 1.1 条文\n内容",
        "title": "测试规范",
        "code": "GB-TEST",
        "file_path": "/tmp/test.pdf",
        "file_name": "test.pdf",
        "file_hash": "",
        "spec_status": "现行",
        "replaced_by_code": "",
        "owner": "admin",
    }
    task.update(overrides)
    progress_store[task_id] = task
    return task


def _big_body(target_bytes=_OVER_LIMIT_BYTES):
    """构造一段超过表单字段上限的中文正文（每字符 3 字节）"""
    unit = "桥面防水层主控项目检验应符合下列规定。"
    n = target_bytes // (len(unit.encode("utf-8")))
    return unit * n


def _phase2_stub(sink):
    """Phase 2 替身：把每次调用记进 sink，不真正跑导入"""
    def _stub(*args, **kwargs):
        sink.append((args, kwargs))
    return _stub


def test_confirm_accepts_body_over_form_field_limit(auth_client, monkeypatch):
    """超过 Starlette 1MB 字段上限的 md 必须能提交成功（本缺陷的回归测试）

    修复前：路由用 `content: str = Form(...)`，raw body 收不到字段 →
    Starlette 先抛 Field exceeded maximum size 或 FastAPI 报 422。
    """
    import app.routes.import_routes as ir

    calls = []
    monkeypatch.setattr(ir, "_process_import_phase2", _phase2_stub(calls))
    task_id = "cc000001"
    _seed_task(task_id)
    body = _big_body()
    assert len(body.encode("utf-8")) > 1024 * 1024, "测试数据必须真的超限才有意义"

    try:
        resp = auth_client.post(
            f"/import/review/{task_id}/confirm",
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        assert resp.status_code == 200, f"超限 md 被拒: {resp.status_code} {resp.text[:200]}"
        # 关键：整份 body 一字不差地到达服务端（未经表单解码变形）
        assert progress_store[task_id]["md_text"] == body
    finally:
        progress_store.pop(task_id, None)


def test_confirm_empty_body_rejected(auth_client, monkeypatch):
    """空 body → 400，且不改动任务状态（不启动 Phase 2）"""
    import app.routes.import_routes as ir

    called = []
    monkeypatch.setattr(ir, "_process_import_phase2", _phase2_stub(called))
    task_id = "cc000002"
    task = _seed_task(task_id)

    try:
        resp = auth_client.post(
            f"/import/review/{task_id}/confirm",
            content=b"",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        assert resp.status_code == 400
        assert called == [], "空 body 不应启动 Phase 2"
        assert task["status"] == "review_needed", "空 body 不应改动任务状态"
    finally:
        progress_store.pop(task_id, None)


def test_confirm_blank_body_rejected(auth_client, monkeypatch):
    """纯空白 body 同样拒绝（避免把空文本写进库）"""
    import app.routes.import_routes as ir

    calls = []
    monkeypatch.setattr(ir, "_process_import_phase2", _phase2_stub(calls))
    task_id = "cc000003"
    _seed_task(task_id)
    try:
        resp = auth_client.post(
            f"/import/review/{task_id}/confirm",
            content="   \n\t  ".encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        assert resp.status_code == 400
    finally:
        progress_store.pop(task_id, None)


def test_review_page_no_longer_posts_md_as_form_field(auth_client):
    """审查页不得再把 md 放在表单字段里回传（否则又会撞 1MB 上限）

    断言的是「机制」而非文案：页面 HTML 中不存在 name="content" 的输入控件。
    """
    task_id = "cc000004"
    _seed_task(task_id, md_text="# 测试规范\n## 1.1 条文\n内容")
    try:
        resp = auth_client.get(f"/import/review/{task_id}")
        assert resp.status_code == 200
        html = resp.text
        assert 'name="content"' not in html, "md 仍以表单字段回传，会再次撞 1MB 上限"
    finally:
        progress_store.pop(task_id, None)
