import uuid
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from app.config import UPLOAD_DIR, OUTPUT_DIR, ADAPTIVE_THRESHOLDS
from app.database import get_db
from app.parser.md_parser import parse_markdown
from app.ocr.pdf_extract import extract_text, is_scanned
from app.classifier.rule_engine import classify_clause, should_use_ai
from app.search.vector_search import VectorStore

router = APIRouter()
progress_store = {}


@router.post("/import/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    code: str = Form(""),
):
    task_id = uuid.uuid4().hex[:8]
    progress_store[task_id] = {"status": "uploading", "progress": 0, "message": "正在上传..."}

    ext = Path(file.filename).suffix.lower()
    save_path = Path(UPLOAD_DIR) / f"{task_id}{ext}"
    content = await file.read()
    save_path.write_bytes(content)

    background_tasks.add_task(
        _process_import, task_id, str(save_path), title, code
    )
    return HTMLResponse(
        f'<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">处理中...</div>'
    )


@router.get("/import/progress/{task_id}")
async def get_progress(request: Request, task_id: str):
    p = progress_store.get(task_id, {"status": "unknown", "progress": 0, "message": "未知任务"})
    from app.main import templates
    return templates.TemplateResponse(request, "partials/import_progress.html", {
        "task_id": task_id, "progress": p,
    })


def _process_import(task_id: str, file_path: str, title: str, code: str):
    """后台任务 Phase 1：OCR(如需) → 暂停等待审查 → 审查后继续 Phase 2"""
    conn = None
    try:
        from app.database import get_connection
        conn = get_connection()
        path = Path(file_path)
        ext = path.suffix.lower()
        progress_store[task_id].update(status="processing", progress=10, message="正在提取文本...")

        # Step 1: 获取 MD 文本
        if ext == ".md":
            md_text = path.read_text(encoding="utf-8")
            # MD 文件直接继续 Phase 2
            _process_import_phase2(task_id, md_text, title, code, file_path, conn)
            return
        elif ext == ".pdf":
            if is_scanned(file_path):
                progress_store[task_id].update(progress=20, message="正在 OCR 识别...")
                from app.ocr.paddle_ocr import ocr_pdf_to_md
                md_path = ocr_pdf_to_md(file_path)
                md_text = Path(md_path).read_text(encoding="utf-8")
            else:
                md_text = extract_text(file_path)
        else:
            progress_store[task_id].update(status="error", message=f"不支持的文件格式: {ext}")
            return

        # PDF 文件：保存 OCR/extract 结果，暂停等待人工审查
        progress_store[task_id]["md_text"] = md_text
        progress_store[task_id]["title"] = title
        progress_store[task_id]["code"] = code
        progress_store[task_id]["file_path"] = file_path
        progress_store[task_id]["file_name"] = path.name
        progress_store[task_id]["conn"] = conn  # 复用连接

        progress_store[task_id].update(
            status="review_needed", progress=50,
            message="OCR 完成，请审查识别结果",
        )
        # conn 不关闭，由 Phase 2 继续使用
        return
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        progress_store[task_id].update(status="error", progress=0, message=str(e))
    finally:
        if progress_store[task_id].get("status") != "review_needed" and conn:
            try:
                conn.close()
            except Exception:
                pass


def _process_import_phase2(task_id: str, md_text: str, title: str, code: str,
                            file_path: str, conn=None):
    """后台任务 Phase 2：解析 → 分类 → 索引（审查确认后调用或 MD 文件直接调用）"""
    own_conn = conn is None
    try:
        from app.database import get_connection
        if own_conn:
            conn = get_connection()

        progress_store[task_id].update(progress=60, message="正在解析条文...")

        # Step 2: 解析条文
        clauses_data = parse_markdown(md_text)

        # Step 3: 规范级分类
        dim1_hierarchy = _detect_hierarchy(code)
        dim1_nature = "推荐性" if "/T" in code else "强制性"

        progress_store[task_id].update(progress=70, message=f"正在分类 {len(clauses_data)} 条条文...")

        # Step 4: 加载分类规则
        rules_rows = conn.execute(
            "SELECT * FROM classification_rules WHERE is_active = 1"
        ).fetchall()
        rules = [dict(r) for r in rules_rows]

        # Step 5: 写入数据库 + 分类 + 向量索引
        output_dir = str(Path(OUTPUT_DIR) / (code or Path(file_path).stem))
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature, source_path, output_dir)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (code or Path(file_path).stem, title or Path(file_path).stem,
             dim1_hierarchy, dim1_nature, file_path, output_dir),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        classified_count = 0
        vs = None
        try:
            vs = VectorStore()
        except Exception:
            pass

        for cd in clauses_data:
            scores = classify_clause(cd["content"], cd.get("parent_path", []), rules)
            conn.execute(
                """INSERT INTO clauses (spec_id, clause_no, title, content, parent_clause)
                   VALUES (?, ?, ?, ?, ?)""",
                (spec_id, cd["clause_no"], cd["title"], cd["content"], None),
            )
            clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for dim in ["dim4", "dim5", "dim6"]:
                if should_use_ai(dim, scores, ADAPTIVE_THRESHOLDS):
                    conn.execute(
                        "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, ?)",
                        (clause_id, dim, scores[dim]),
                    )
                else:
                    classified_count += 1

            if vs is not None:
                try:
                    dim_scores_str = ",".join(f"{k}={v:.2f}" for k, v in scores.items())
                    embed_text = f"{code or ''} {title or ''} [{cd['clause_no']}] {cd['title'] or ''} {cd['content']}"
                    vs.index_clause(clause_id, spec_id, embed_text, dim_scores_str)
                except Exception:
                    vs = None

        conn.execute("UPDATE specifications SET clause_count = ? WHERE id = ?",
                    (len(clauses_data), spec_id))
        conn.commit()

        progress_store[task_id].update(
            status="done", progress=100,
            message=f"导入完成：{len(clauses_data)} 条条文已解析，{classified_count} 个维度已分类"
        )
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        progress_store[task_id].update(status="error", progress=0, message=str(e))
    finally:
        if conn and own_conn:
            try:
                conn.close()
            except Exception:
                pass


def _detect_hierarchy(code: str) -> str:
    if code.startswith("GB"):
        return "国家标准"
    elif code.startswith("JGJ") or code.startswith("CJJ"):
        return "行业标准"
    elif code.startswith("DB"):
        return "地方标准"
    elif code.startswith("T/"):
        return "团体标准"
    return "企业标准"


# ═══════════════════════════════════════════
# OCR 审查
# ═══════════════════════════════════════════

@router.get("/import/review/{task_id}")
async def review_page(request: Request, task_id: str):
    """OCR 审查页"""
    task = progress_store.get(task_id)
    if not task or task.get("status") != "review_needed":
        from app.main import templates
        return templates.TemplateResponse(request, "base.html", {
            "left_content": "partials/tree_panel.html",
            "center_content": "partials/welcome.html",
            "right_content": "partials/qa_panel.html",
        })

    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/ocr_review.html",
        "right_content": "partials/qa_panel.html",
        "task_id": task_id,
    })


@router.get("/import/review/{task_id}/content")
async def review_content(request: Request, task_id: str):
    """获取 OCR 原始文本"""
    task = progress_store.get(task_id)
    if not task or "md_text" not in task:
        return JSONResponse({"detail": "任务不存在或已过期"}, status_code=404)
    return {"content": task["md_text"], "file_name": task.get("file_name", "")}


@router.post("/import/review/{task_id}/confirm")
async def confirm_review(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
):
    """审查确认：提交修改后的 Markdown 文本，继续 Phase 2"""
    task = progress_store.get(task_id)
    if not task:
        return HTMLResponse("<p style='color:red'>任务不存在或已过期</p>")

    title = task.get("title", "")
    code = task.get("code", "")
    file_path = task.get("file_path", "")
    conn = task.pop("conn", None)

    # 更新为审查后的文本
    task["md_text"] = content
    task.update(status="processing", progress=55, message="审查完成，正在继续导入...")

    # 启动 Phase 2
    background_tasks.add_task(
        _process_import_phase2, task_id, content, title, code, file_path, conn
    )

    return HTMLResponse(
        f"""<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">
        <p>审查完成，正在继续导入...</p></div>"""
    )


@router.get("/tree/all")
async def get_tree(request: Request):
    """返回分类树数据"""
    # dim1/2/3 在 specifications 表，dim4/5/6 在 clauses 表
    spec_dims = [
        {"key": "dim1_hierarchy", "label": "规范层级", "field": "dim1_hierarchy"},
        {"key": "dim1_nature", "label": "规范性质", "field": "dim1_nature"},
        {"key": "dim2_stage", "label": "工程阶段", "field": "dim2_stage"},
        {"key": "dim3_usage", "label": "工程用途", "field": "dim3_usage"},
    ]
    clause_dims = [
        {"key": "dim4_specialty", "label": "所属专业", "field": "dim4_specialty"},
        {"key": "dim5_location", "label": "工程部位", "field": "dim5_location"},
        {"key": "dim6_material", "label": "材料/工艺", "field": "dim6_material"},
    ]
    result = []

    with get_db() as conn:
        for dim in spec_dims:
            rows = conn.execute(
                f"SELECT {dim['field']}, COUNT(*) as cnt FROM specifications "
                f"WHERE {dim['field']} IS NOT NULL AND {dim['field']} != '' "
                f"GROUP BY {dim['field']} ORDER BY cnt DESC LIMIT 30"
            ).fetchall()
            nodes = _build_tree_nodes(rows, dim["field"])
            result.append({"key": dim["key"], "label": dim["label"], "nodes": nodes})

    with get_db() as conn:
        for dim in clause_dims:
            rows = conn.execute(
                f"SELECT {dim['field']}, COUNT(*) as cnt FROM clauses "
                f"WHERE {dim['field']} IS NOT NULL AND {dim['field']} != '' "
                f"GROUP BY {dim['field']} ORDER BY cnt DESC LIMIT 30"
            ).fetchall()
            nodes = _build_tree_nodes(rows, dim["field"])
            result.append({"key": dim["key"], "label": dim["label"], "nodes": nodes})
    return result


def _build_tree_nodes(rows, field: str) -> list[dict]:
    """将查询结果转为树节点（处理逗号分隔的多值字段）"""
    nodes = []
    for r in rows:
        val = r[field]
        if val and "," in val:
            for sub in val.split(","):
                sub = sub.strip()
                if sub:
                    nodes.append({"label": sub, "value": sub, "count": r["cnt"], "children": []})
        else:
            nodes.append({"label": val or "(未分类)", "value": val, "count": r["cnt"], "children": []})
    return nodes
