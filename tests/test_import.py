def test_import_page_protected(client):
    resp = client.get("/import", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)


def _process_import_and_get_state(monkeypatch, tmp_path, task_id, force_ocr,
                                  is_scanned_val, use_ocr_client):
    """执行 _process_import，mock is_scanned 与 create_ocr_client，返回 progress 状态"""
    db_path = tmp_path / "test_force_ocr.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    import io
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake scanned doc")

    from app.routes import import_routes

    # 模拟 upload 流程已创建的任务条目
    import_routes.progress_store[task_id] = {
        "status": "uploading", "progress": 0, "message": "正在上传...",
    }
    monkeypatch.setattr(import_routes, "is_scanned", lambda p: is_scanned_val)

    calls = {"ocr": 0, "extract": 0}

    class FakeOCRClient:
        def ocr_pdf_to_md(self, path, output_dir=None):
            calls["ocr"] += 1
            md = tmp_path / f"ocr_{task_id}.md"
            md.write_text("# OCR 结果\n\n1.0.1 测试条文", encoding="utf-8")
            return str(md)

    def fake_extract(p):
        calls["extract"] += 1
        return "纯文本提取结果"

    monkeypatch.setattr(import_routes, "extract_text", fake_extract)
    if use_ocr_client:
        monkeypatch.setattr(
            "app.ocr.paddle_api.create_ocr_client", lambda: FakeOCRClient()
        )

    import_routes._process_import(task_id, str(pdf), "标题", "JGJ 107", force_ocr=force_ocr)
    state = import_routes.progress_store[task_id]["status"]
    return calls, state


def test_force_ocr_triggers_ocr_when_not_scanned(monkeypatch, tmp_path):
    """force_ocr=True 且 is_scanned=False → 仍走 OCR（解决半扫描 PDF）"""
    calls, state = _process_import_and_get_state(
        monkeypatch, tmp_path, "task-focr", force_ocr=True,
        is_scanned_val=False, use_ocr_client=True,
    )
    assert calls["ocr"] == 1
    assert calls["extract"] == 0
    assert state == "review_needed"


def test_no_force_ocr_uses_extract_when_not_scanned(monkeypatch, tmp_path):
    """force_ocr=False 且 is_scanned=False → 走 extract_text（默认行为不变）"""
    calls, state = _process_import_and_get_state(
        monkeypatch, tmp_path, "task-noforce", force_ocr=False,
        is_scanned_val=False, use_ocr_client=False,
    )
    assert calls["ocr"] == 0
    assert calls["extract"] == 1
    assert state == "review_needed"


def test_upload_no_file_authenticated(auth_client):
    resp = auth_client.post("/import/upload")
    assert resp.status_code in (400, 422)


def test_upload_markdown(auth_client, monkeypatch, tmp_path):
    """测试上传 MD 文件导入流程"""
    db_path = tmp_path / "test_import.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    # 创建测试用的 MD 文件
    md_content = """# 测试规范

## 1 总则

### 1.1 一般规定

混凝土施工应满足设计要求。

### 1.2 材料

钢筋进场时按标准检验。
"""
    import io
    md_file = io.BytesIO(md_content.encode("utf-8"))
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("test.md", md_file, "text/markdown")},
        data={"code": "GB 99999", "title": "测试规范"},
    )
    # Should return 200 with progress HTML
    assert resp.status_code == 200
    assert "处理中" in resp.text or "import-status" in resp.text


def test_upload_duplicate_rejected(auth_client, monkeypatch, tmp_path):
    """重复上传相同文件应被拒绝"""
    db_path = tmp_path / "test_dup.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()

    # 预先插入一条带 file_hash 的规范记录
    import hashlib
    md_content = "# 重复测试\n## 1.1 条文\n内容".encode("utf-8")
    file_hash = hashlib.sha256(md_content).hexdigest()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature, file_hash)
               VALUES (?, ?, ?, ?, ?)""",
            ("GB-DUP", "已存在的规范", "国家标准", "强制性", file_hash),
        )

    # 尝试上传相同内容的文件
    import io
    md_file = io.BytesIO(md_content)
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("dup.md", md_file, "text/markdown")},
        data={"code": "GB-DUP-2", "title": "重复规范"},
    )
    assert resp.status_code == 200
    # 应该提示重复
    assert "已导入" in resp.text or "重复" in resp.text or "已存在" in resp.text
