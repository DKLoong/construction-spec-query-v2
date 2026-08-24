import pytest


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


def test_process_import_cleans_ocr_text_before_review(monkeypatch, tmp_path):
    """_process_import 在进入审查前先保守清洗 OCR 文本（页码行/纯数字行被删除）"""
    db_path = tmp_path / "test_clean.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake scanned doc")

    from app.routes import import_routes
    task_id = "task-clean"
    import_routes.progress_store[task_id] = {
        "status": "uploading", "progress": 0,
    }
    monkeypatch.setattr(import_routes, "is_scanned", lambda p: True)

    class FakeOCRClient:
        def ocr_pdf_to_md(self, path, output_dir=None):
            md = tmp_path / "ocr_clean.md"
            md.write_text("## 第1页\n\n第 1 页\n12\n正文内容", encoding="utf-8")
            return str(md)

    monkeypatch.setattr(
        "app.ocr.paddle_api.create_ocr_client", lambda: FakeOCRClient()
    )

    import_routes._process_import(task_id, str(pdf), "标题", "GB 1234", force_ocr=False)
    stored = import_routes.progress_store[task_id]["md_text"]
    assert "第 1 页" not in stored
    assert "12" not in stored
    assert "正文内容" in stored
    # `## 第1页` 结构分隔标记保留（供审查/封面过滤使用）
    assert "## 第1页" in stored
    assert import_routes.progress_store[task_id]["status"] == "review_needed"


def test_filter_cover_clauses_drops_cover_content():
    """_filter_cover_clauses 丢弃封面/出版信息页脏数据条文，保留正常条文"""
    from app.routes.import_routes import _filter_cover_clauses
    clauses = [
        {
            "clause_no": "第1页",
            "title": "第1页",
            "content": "ICS 77.140.60\n中华人民共和国国家标准\n代替 GB/T 1499.1—2008",
        },
        {"clause_no": "1.0.1", "title": "", "content": "本标准自发布之日起实施。"},
    ]
    result = _filter_cover_clauses(clauses)
    assert len(result) == 1
    assert result[0]["clause_no"] == "1.0.1"


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


def test_copy_ocr_images_copies_to_output_dir(tmp_path):
    """OCR 图片复制到 spec.output_dir（code 非空时目录不同）"""
    from app.routes.import_routes import _copy_ocr_images

    ocr_dir = tmp_path / "ocr_task"
    imgs = ocr_dir / "imgs"
    imgs.mkdir(parents=True)
    (imgs / "a.jpg").write_bytes(b"img-a")

    out_dir = tmp_path / "spec_out"
    _copy_ocr_images(ocr_dir, out_dir)

    assert (out_dir / "imgs" / "a.jpg").read_bytes() == b"img-a"


def test_copy_ocr_images_skips_same_dir(tmp_path):
    """ocr_dir == out_dir（未填 code）时不复制、不报错"""
    from app.routes.import_routes import _copy_ocr_images

    d = tmp_path / "same"
    imgs = d / "imgs"
    imgs.mkdir(parents=True)
    (imgs / "a.jpg").write_bytes(b"img-a")

    _copy_ocr_images(d, d)

    assert (d / "imgs" / "a.jpg").read_bytes() == b"img-a"


def test_copy_ocr_images_no_imgs_dir(tmp_path):
    """OCR 目录无 imgs/ 时不报错"""
    from app.routes.import_routes import _copy_ocr_images

    ocr_dir = tmp_path / "ocr_noimg"
    ocr_dir.mkdir()
    out_dir = tmp_path / "out_noimg"

    _copy_ocr_images(ocr_dir, out_dir)

    assert not (out_dir / "imgs").exists()


# ═══════════════════════════════════════════
# 取消导入（cancel review）
# ═══════════════════════════════════════════

@pytest.fixture(autouse=True)
def _clean_progress_store():
    """每个测试结束后清理 progress_store（模块级全局字典，避免串扰）"""
    yield
    from app.routes import import_routes
    import_routes.progress_store.clear()


def _setup_cancel_env(monkeypatch, tmp_path, task_id):
    """把 UPLOAD_DIR / OUTPUT_DIR 指到临时目录，返回 (upload_dir, output_dir)"""
    from app.routes import import_routes

    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    upload_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr(import_routes, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(import_routes, "OUTPUT_DIR", str(output_dir))
    return upload_dir, output_dir


def test_cancel_review_cleans_files_and_progress(monkeypatch, tmp_path, auth_client):
    """取消导入：删除上传文件 + OCR 结果目录（含 imgs/），移除内存任务，HX-Redirect 回主页"""
    from app.routes import import_routes

    task_id = "a1b2c3d4"
    upload_dir, output_dir = _setup_cancel_env(monkeypatch, tmp_path, task_id)

    # 上传的原始 PDF
    upload_dir.joinpath(f"{task_id}.pdf").write_bytes(b"%PDF-1.4 fake")
    # OCR 结果目录：{task_id}.md + imgs/ 已下载图片
    out_task = output_dir / task_id
    (out_task / "imgs").mkdir(parents=True)
    (out_task / "imgs" / "a.jpg").write_bytes(b"img-a")
    (out_task / f"{task_id}.md").write_text("# OCR 结果", encoding="utf-8")
    # 内存任务（审查待确认状态）
    import_routes.progress_store[task_id] = {
        "status": "review_needed",
        "file_path": str(upload_dir / f"{task_id}.pdf"),
    }

    resp = auth_client.post(f"/import/review/{task_id}/cancel")

    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/"
    assert not upload_dir.joinpath(f"{task_id}.pdf").exists()
    assert not out_task.exists()
    assert task_id not in import_routes.progress_store


def test_cancel_review_invalid_task_id_rejected(monkeypatch, tmp_path, auth_client):
    """非法 task_id（非 8 位十六进制）返回 404 且不清理"""
    task_id = "a1b2c3d"  # 仅 7 位
    upload_dir, _ = _setup_cancel_env(monkeypatch, tmp_path, task_id)
    upload_dir.joinpath(f"{task_id}.pdf").write_bytes(b"%PDF")

    resp = auth_client.post(f"/import/review/{task_id}/cancel")

    assert resp.status_code == 404
    assert upload_dir.joinpath(f"{task_id}.pdf").exists()


def test_cancel_review_idempotent_with_stale_files(monkeypatch, tmp_path, auth_client):
    """任务不存在（服务重启后 progress_store 清空）但磁盘有残留 → 仍清理（幂等）"""
    task_id = "a1b2c3d4"
    upload_dir, output_dir = _setup_cancel_env(monkeypatch, tmp_path, task_id)
    upload_dir.joinpath(f"{task_id}.pdf").write_bytes(b"%PDF")
    out_task = output_dir / task_id
    out_task.mkdir()

    from app.routes import import_routes
    import_routes.progress_store.pop(task_id, None)  # 模拟服务重启

    resp = auth_client.post(f"/import/review/{task_id}/cancel")

    assert resp.status_code == 200
    assert not upload_dir.joinpath(f"{task_id}.pdf").exists()
    assert not out_task.exists()


def test_cancel_review_without_files_is_noop(monkeypatch, tmp_path, auth_client):
    """无磁盘残留时取消也不报错，正常移除任务"""
    from app.routes import import_routes

    task_id = "a1b2c3d4"
    _setup_cancel_env(monkeypatch, tmp_path, task_id)
    import_routes.progress_store[task_id] = {"status": "review_needed"}

    resp = auth_client.post(f"/import/review/{task_id}/cancel")

    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/"
    assert task_id not in import_routes.progress_store


def test_cancel_review_rejects_other_owner(monkeypatch, tmp_path, auth_client):
    """非属主取消他人任务 → 403，文件不清理"""
    from app.routes import import_routes

    task_id = "a1b2c3d4"
    upload_dir, output_dir = _setup_cancel_env(monkeypatch, tmp_path, task_id)
    upload_dir.joinpath(f"{task_id}.pdf").write_bytes(b"%PDF")
    import_routes.progress_store[task_id] = {
        "status": "review_needed",
        "owner": "someone_else",
        "file_path": str(upload_dir / f"{task_id}.pdf"),
    }

    resp = auth_client.post(f"/import/review/{task_id}/cancel")

    assert resp.status_code == 403
    assert upload_dir.joinpath(f"{task_id}.pdf").exists()
    assert task_id in import_routes.progress_store


def test_cancel_review_rejects_done_status(monkeypatch, tmp_path, auth_client):
    """已完成导入的任务不可取消 → 409，文件保留（防删已入库规范源文件）"""
    from app.routes import import_routes

    task_id = "a1b2c3d4"
    upload_dir, output_dir = _setup_cancel_env(monkeypatch, tmp_path, task_id)
    upload_dir.joinpath(f"{task_id}.pdf").write_bytes(b"%PDF")
    import_routes.progress_store[task_id] = {
        "status": "done",
        "owner": "admin",
        "file_path": str(upload_dir / f"{task_id}.pdf"),
    }

    resp = auth_client.post(f"/import/review/{task_id}/cancel")

    assert resp.status_code == 409
    assert upload_dir.joinpath(f"{task_id}.pdf").exists()


def test_cancel_review_skips_db_referenced_output_dir(monkeypatch, tmp_path, auth_client):
    """outputs/{task_id}/ 被 specifications.output_dir 引用时不可删除（防误删已入库规范）"""
    from app.routes import import_routes
    from app.database import get_db, init_db

    db_path = tmp_path / "test_cancel_ref.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()

    task_id = "a1b2c3d4"
    upload_dir, output_dir = _setup_cancel_env(monkeypatch, tmp_path, task_id)
    out_task = output_dir / task_id
    out_task.mkdir()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, output_dir) VALUES (?, ?, ?)",
            ("a1b2c3d4", "八位hex编号规范", str(out_task)),
        )
    import_routes.progress_store[task_id] = {"status": "review_needed", "owner": "admin"}

    resp = auth_client.post(f"/import/review/{task_id}/cancel")

    assert resp.status_code == 200
    assert out_task.exists(), "被 DB 引用的 output_dir 不应被删除"
    assert task_id not in import_routes.progress_store
