"""OCR 审查流程测试"""
import pytest


def test_review_page_redirects_when_no_task(auth_client):
    """无有效任务时审查页显示欢迎页"""
    resp = auth_client.get("/import/review/nonexistent123")
    assert resp.status_code == 200
    # 应显示欢迎页（无有效任务）
    assert "welcome" in resp.text.lower() or "管理" in resp.text


def test_review_content_endpoint_no_task(auth_client):
    """无任务时返回 404"""
    resp = auth_client.get("/import/review/nonexistent123/content")
    assert resp.status_code == 404


def test_confirm_review_resumes_import(auth_client, monkeypatch, tmp_path):
    """审查确认后启动 Phase 2 并显示进度"""
    from app.routes.import_routes import progress_store

    task_id = "test_review_confirm"
    progress_store[task_id] = {
        "status": "review_needed",
        "progress": 50,
        "message": "OCR 完成",
        "md_text": "# 测试规范\n## 1.1 条文\n内容",
        "title": "测试规范",
        "code": "GB-TEST",
        "file_path": "/tmp/test.pdf",
        "file_name": "test.pdf",
    }

    db_path = tmp_path / "test_ocr_confirm.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    # Mock VectorStore
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.__init__", lambda self: None,
    )
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.index_clause",
        lambda self, a, b, c, d="": None,
    )

    resp = auth_client.post(
        f"/import/review/{task_id}/confirm",
        data={"content": "# 测试规范（审查后）\n## 1.1 修正条文\n修正内容"},
    )
    assert resp.status_code == 200
    assert "继续导入" in resp.text or "import-status" in resp.text

    # 验证状态已更新
    task = progress_store.get(task_id, {})
    assert task.get("status") in ("processing", "done", "error")


def test_confirm_review_rejects_duplicate(auth_client):
    """审查确认时重复点击应被拒绝"""
    from app.routes.import_routes import progress_store

    task_id = "test_review_dup_confirm"
    # 模拟已经处于 processing 状态的任务
    progress_store[task_id] = {
        "status": "processing",
        "progress": 55,
        "message": "审查完成，正在继续导入...",
        "md_text": "# 测试规范\n## 1.1 条文\n内容",
        "title": "测试规范",
        "code": "GB-TEST",
        "file_path": "/tmp/test.pdf",
        "file_name": "test.pdf",
    }

    resp = auth_client.post(
        f"/import/review/{task_id}/confirm",
        data={"content": "# 再次提交的内容"},
    )
    assert resp.status_code == 200
    # 应提示正在处理中
    assert "处理中" in resp.text or "请勿重复" in resp.text
