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


def test_nearest_ancestor_id_walks_dot_segment_prefixes():
    """parent_clause 取「最近**现存**祖先」：按点段前缀由长到短回溯，首个存在者胜。

    批一删掉了「只有标题、无自身正文」的章节行 ⇒ `21.4` 可能整行不存在，
    `21.4.1` 的最近现存祖先退到 `21`（用户实测：`21.3.x` 直跳 `21.4.1`）。
    父级编号必是子级编号的**段前缀** —— 导入级断言
    `test_imported_parent_links_are_segment_prefixes` 正依赖这一不变量
    （本用例只验 helper 的返回值，那条验库里已落地的行）。
    """
    from app.routes.import_routes import _nearest_ancestor_id
    no_to_id = {"6": 10, "6.3": 11, "6.3.1": 12}
    # 正常场景：直接父级存在
    assert _nearest_ancestor_id(no_to_id, "6.3.2") == 11
    # 边界场景：中间层缺失 → 跨级回溯到更短的现存祖先
    assert _nearest_ancestor_id(no_to_id, "6.9.1") == 10
    # 边界场景：顶层条文（只一段，无前缀可试）→ NULL
    assert _nearest_ancestor_id(no_to_id, "6") is None
    # 异常场景：无任何现存祖先
    assert _nearest_ancestor_id(no_to_id, "8.1.1") is None
    assert _nearest_ancestor_id(no_to_id, "") is None


# ═══════════════════════════════════════════
# 导入级：父子链不变量（① 无悬空父引用 ② 父级编号是子级编号的段前缀）
# ═══════════════════════════════════════════

# 夹具刻意同时含两种条文：
#   - `5`/`5.1`/`5.2` 各有自身正文 ⇒ 会作为条文入库，于是 `5.1.1` 有**现存**祖先链；
#   - `9.1.1` 的段前缀祖先（`9`、`9.1`）在本规范里**不存在**（批一不存「只有标题、
#     无自身正文」的章节行）⇒ parent_clause 必须是 NULL，而不是「随便指一个现存行」。
# 两半边缺任一，下面的断言都会退化成恒真（孤儿计数只查外键存在性）。
_ANCESTOR_MD = """# 测试规范

### 9.1.1 孤立条文

本条无任何现存祖先。

## 5 混凝土分项工程

本章适用于混凝土分项工程的施工。

### 5.1 模板

本节规定模板工程的要求。

#### 5.1.1 模板设计

模板及其支架应根据工程结构形式进行设计。

### 5.2 钢筋

本节规定钢筋工程的要求。

#### 5.2.1 原材料

钢筋进场时应抽取试件作屈服强度检验。
"""


def _drive_real_import(isolated_paths, monkeypatch, md_text: str,
                       task_id: str = "anc00001") -> dict[str, dict]:
    """走**真实导入路径**（`_process_import` → phase2 写库），返回 `{clause_no: 行}`

    行内含 `section_path`（原文快照，供展示）与 `breadcrumb`（派生 FTS 文本）两列，
    供 U16 核对「原文不动、派生文本归一化」这条边界。
    """
    from app.database import get_db, init_db
    from app.routes import import_routes as ir

    init_db()
    md_path = isolated_paths / "uploads" / "ancestor_chain.md"
    md_path.write_text(md_text, encoding="utf-8")
    # 不加载真实 BGE 模型：无模型机器上 `get_model()` 返回 None ⇒ 向量阶段整体落空
    monkeypatch.setattr("app.ai.embedding.get_model", lambda: object())
    monkeypatch.setattr("app.ai.embedding.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    ir.progress_store[task_id] = {"status": "processing", "progress": 0, "owner": "t"}
    try:
        ir._process_import(task_id, str(md_path), "祖先链测试规范", "GB/T 11111-2020")
        state = dict(ir.progress_store[task_id])
        assert state["status"] == "done", f"导入未完成：{state}"
    finally:
        ir.progress_store.pop(task_id, None)

    with get_db() as conn:
        return {r["clause_no"]: dict(r) for r in conn.execute(
            "SELECT id, clause_no, parent_clause, section_path, breadcrumb FROM clauses")}


def test_imported_parent_links_are_segment_prefixes(isolated_paths, monkeypatch):
    """导入后父子链的两条不变量：① 悬空父引用为 0 ② 父级编号是子级编号的段前缀。

    为什么必须是**导入级**：`test_nearest_ancestor_id_walks_dot_segment_prefixes`
    只把一个手搭的 dict 交给 helper 并断言它返回哪个 id，**从不读表** —— 写入侧
    （`import_routes` 的 `no_to_id` 预载/回填、INSERT 的 `parent_clause` 参数）改了
    也没人发现。而孤儿计数（`_count_orphan_parent`）**只验外键存在性**：写入方填一个
    库里存在的 id 就恒绿，哪怕它与编号毫无关系（更早的无关条文）。两条合起来才咬得住。
    """
    from app.database import get_db
    from app.maintenance.health_check import _count_orphan_parent

    rows = _drive_real_import(isolated_paths, monkeypatch, _ANCESTOR_MD)

    # 夹具前提：条文集合与祖先链都得真的产生出来（否则下面两条断言形同虚设）
    # ⚠️ T20（Task 17 / 改动⑤）后夹具多一条 `测试规范`：`_ANCESTOR_MD` 的首行
    #    `# 测试规范` 是无编号标题（不是候选行），原先随「空栈 flush」被丢弃；
    #    改动⑤ 把它保留为一块 `is_non_clause=True` 的隐藏块，故它**确实入库**。
    #    它无父无子，不影响下面两条关于 `parent_clause` 的断言，也不影响父子链不变量。
    assert set(rows) == {"9.1.1", "5", "5.1", "5.1.1", "5.2", "5.2.1", "测试规范"}, \
        f"夹具条文集合不符：{sorted(rows)}"
    assert rows["5.1.1"]["parent_clause"] == rows["5.1"]["id"], \
        "有现存祖先的条文必须挂到该祖先（最近现存祖先）"
    assert rows["9.1.1"]["parent_clause"] is None, \
        "段前缀祖先都不存在的条文，parent_clause 必须为 NULL"

    # ① 悬空父引用（复用生产判据：parent_clause 非空但无对应父行）
    assert _count_orphan_parent() == 0, "导入后出现悬空 parent_clause"

    # ② 段前缀不变量：读**库里已落地的行**（JOIN clauses 自身），不读 helper 返回值
    with get_db() as conn:
        pairs = conn.execute(
            """SELECT c.clause_no AS child_no, p.clause_no AS parent_no
               FROM clauses c JOIN clauses p ON p.id = c.parent_clause"""
        ).fetchall()
    # 夹具前提：四条链都真的挂上了（不按「父级是谁」断言——父级身份由下面的段前缀
    # 不变量逐对判定，否则本条会把该不变量的判别力整个吃掉）
    assert sorted(c for c, _ in pairs) == ["5.1", "5.1.1", "5.2", "5.2.1"], \
        f"夹具父子链与预期不符：{[tuple(p) for p in pairs]}"
    for child_no, parent_no in pairs:
        segs = [s for s in child_no.split(".") if s]
        prefixes = {".".join(segs[:k]) for k in range(1, len(segs))}
        assert parent_no in prefixes, \
            f"父级编号 {parent_no!r} 不是子级编号 {child_no!r} 的段前缀（候选：{sorted(prefixes)}）"


def test_upload_no_file_authenticated(auth_client):
    resp = auth_client.post("/import/upload")
    assert resp.status_code in (400, 422)


def _multipart_without_filename(data: bytes = b"# T\n\n## 1 \xe6\x80\xbb\xe5\x88\x99\n",
                                field: str = "file"):
    """构造**没有 filename= 参数**的 multipart part。

    注意：`filename=""`（空串）**不是**这种情况——`Path("")` 不抛异常。只有整个
    part 缺 filename 时 Starlette 才给出 `filename=None`（其注解即 `str | None`）。
    """
    boundary = "----noFilenameBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"\r\n'
        f"Content-Type: text/markdown\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def test_upload_without_filename_never_5xx(auth_client, monkeypatch, tmp_path):
    """畸形上传（part 缺 filename）不得 5xx

    实测：Starlette 只在 part **带** `filename=` 时才构造 UploadFile，否则当普通
    表单字段 → FastAPI 校验直接拒 → 422。此处锁住「不得 500」这个契约。
    """
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "nf.db"))
    from app.database import init_db
    init_db()

    body, content_type = _multipart_without_filename()
    resp = auth_client.post("/import/upload", content=body,
                            headers={"content-type": content_type})
    assert resp.status_code < 500, f"畸形上传不应 5xx，实际 {resp.status_code}"


def test_upload_null_filename_returns_400(monkeypatch, tmp_path):
    """直接以 filename=None 调用路由须返回 400（防御性校验，非可达 bug）

    经 HTTP **不可达**：Starlette 的 MultiPartParser 仅在 part 带 `filename=` 时
    构造 UploadFile，且 filename 恒为解码后的 str（可能空串、永不为 None）。
    但 `UploadFile.filename` 的库声明是 `str | None`，据此直接 `Path(...)` 推导扩展名
    属于「假定库声明之外的输入形态」；按全局规则 1.1（外部输入必须校验）加守卫。
    本测试是该守卫的唯一回归入口（HTTP 层到不了）。
    """
    import asyncio
    import io

    from fastapi import BackgroundTasks, UploadFile
    from starlette.requests import Request

    from app.routes.import_routes import upload_file

    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "nf2.db"))
    from app.database import init_db
    init_db()

    req = Request({"type": "http", "method": "POST", "path": "/import/upload",
                   "headers": []})
    req.state.username = "tester"
    upload = UploadFile(filename=None, file=io.BytesIO(b"x"))

    resp = asyncio.run(upload_file(req, BackgroundTasks(), upload))

    assert resp.status_code == 400, f"应返回 400，实际 {resp.status_code}"


def test_upload_markdown(auth_client, monkeypatch, tmp_path):
    """测试上传 MD 文件导入流程"""
    db_path = tmp_path / "test_import.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    # ⚠ 只 patch DATABASE_PATH 会让本用例把夹具规范真写进**真实**存储：导入流程
    #   还会写向量（LANCE_DB_PATH）与上传文件/输出目录（UPLOAD_DIR/OUTPUT_DIR）。
    #   这三处常量都绑定在**消费方模块**的命名空间里（C-4：`from app.config import X`
    #   在导入时即绑定），故必须按消费方模块名 patch，patch `app.config.*` 无效。
    monkeypatch.setattr(
        "app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_db"))
    monkeypatch.setattr(
        "app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        "app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "outputs"))
    for d in ("lance_db", "uploads", "outputs"):   # 真实目录由 app.config 导入时创建
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
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


# ═══════════════════════════════════════════
# U16：导入级核对「section_path 原文不动 / breadcrumb 派生文本归一化」
# ═══════════════════════════════════════════

#: 源文件把节名写成**词内空格**（CJJ2 夹具里 `## 1 总 则`、`## 6 钢 筋`、
#: `## 12 支 座` 就是这种字面）。展示必须保留原文，检索必须能查到。
_SPACED_SECTION_MD = """# 1 总 则

1.0.1 适用范围

本规范适用于城市桥梁工程。

## 6 钢 筋

6.1 一般规定

6.1.1 原材料

进场材料应抽取试件作检验。
"""


def test_imported_section_path_keeps_source_spacing_while_breadcrumb_is_normalized(
        isolated_paths, monkeypatch):
    """原文/派生的**双向**边界（U16 / D13）：

    - `section_path`（存储列 + 详情/列表展示用）：**逐字保留**源文件的词内空格
      （`1 总 则`）—— 归一化只许发生在派生文本里；
    - `breadcrumb`（FTS 派生列）：折叠词内空格，使 jieba 产出整词 `总则`。

    两条必须同时断言：只钉其中一条时，另一条被「顺手统一」都无人发现
    （把 section_path 也归一化 = 破坏源文保真；漏归一化 = 查不到该节）。
    """
    rows = _drive_real_import(isolated_paths, monkeypatch, _SPACED_SECTION_MD,
                              task_id="u16imp01")

    # 夹具前提：两个带空格的节名都真的进了祖先链（否则下面的断言形同虚设）
    assert rows["1.0.1"]["section_path"] == "1 总 则", \
        f"section_path 必须保留源文件原文: {rows['1.0.1']['section_path']!r}"
    assert rows["6.1.1"]["section_path"] == "6 钢 筋 > 6.1 一般规定", \
        f"多级路径每级都要保真: {rows['6.1.1']['section_path']!r}"

    # 派生列：空格被折叠 → jieba 得到整词
    assert "总则" in rows["1.0.1"]["breadcrumb"].split(), \
        f"面包屑未产出整词「总则」: {rows['1.0.1']['breadcrumb']!r}"
    assert "钢筋" in rows["6.1.1"]["breadcrumb"].split(), \
        f"面包屑未产出整词「钢筋」: {rows['6.1.1']['breadcrumb']!r}"
    # 折叠只发生在词内：编号与节名之间的空白保留（`6 钢筋` 而非 `6钢筋`）
    assert rows["1.0.1"]["breadcrumb"].split()[:1] == ["1"]
    assert " ".join(rows["6.1.1"]["breadcrumb"].split()).startswith("6 钢筋"), \
        f"编号与节名之间的空白不应被折叠: {rows['6.1.1']['breadcrumb']!r}"
