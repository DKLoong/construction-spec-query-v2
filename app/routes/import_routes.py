import uuid
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from app.config import UPLOAD_DIR, OUTPUT_DIR, ADAPTIVE_THRESHOLDS
from app.database import get_db
from app.parser.md_parser import parse_markdown
from app.ocr.pdf_extract import extract_text, is_scanned
from app.classifier.rule_engine import classify_clause, should_use_ai
from app.classifier.batch_queue import add_to_queue
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
    return templates.TemplateResponse("partials/import_progress.html", {
        "request": request, "task_id": task_id, "progress": p,
    })


def _process_import(task_id: str, file_path: str, title: str, code: str):
    """后台任务：OCR(如需) -> 解析 -> 分类 -> 索引"""
    try:
        path = Path(file_path)
        ext = path.suffix.lower()
        progress_store[task_id].update(status="processing", progress=10, message="正在提取文本...")

        # Step 1: 获取 MD 文本
        if ext == ".md":
            md_text = path.read_text(encoding="utf-8")
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

        progress_store[task_id].update(progress=40, message="正在解析条文...")

        # Step 2: 解析条文
        clauses_data = parse_markdown(md_text)

        # Step 3: 规范级分类
        dim1_hierarchy = _detect_hierarchy(code)
        dim1_nature = "推荐性" if "/T" in code else "强制性"

        progress_store[task_id].update(progress=60, message=f"正在分类 {len(clauses_data)} 条条文...")

        # Step 4: 写入数据库 + 分类 + 向量索引
        output_dir = str(Path(OUTPUT_DIR) / (code or path.stem))
        rules = _load_active_rules()
        vs = VectorStore()

        with get_db() as conn:
            conn.execute(
                """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature, source_path, output_dir)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (code or path.stem, title or path.stem, dim1_hierarchy, dim1_nature, file_path, output_dir),
            )
            spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            classified_count = 0
            for cd in clauses_data:
                scores = classify_clause(cd["content"], cd.get("parent_path", []), rules)
                conn.execute(
                    """INSERT INTO clauses (spec_id, clause_no, title, content, parent_clause)
                       VALUES (?, ?, ?, ?, ?)""",
                    (spec_id, cd["clause_no"], cd["title"], cd["content"], None),
                )
                clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # 向量索引（embedding 不可用时跳过）
                try:
                    dim_scores_str = ",".join(f"{k}={v:.2f}" for k, v in scores.items())
                    vs.index_clause(clause_id, spec_id,
                                    f"[{cd['clause_no']}] {cd['title'] or ''} {cd['content']}",
                                    dim_scores_str)
                except Exception:
                    pass

                # 低置信度入队；高于阈值的计入 classified_count
                for dim in ["dim4", "dim5", "dim6"]:
                    if should_use_ai(dim, scores, ADAPTIVE_THRESHOLDS):
                        add_to_queue(clause_id, dim, scores[dim])
                    else:
                        classified_count += 1

            conn.execute("UPDATE specifications SET clause_count = ? WHERE id = ?",
                        (len(clauses_data), spec_id))

        progress_store[task_id].update(
            status="done", progress=100,
            message=f"导入完成：{len(clauses_data)} 条条文已解析，{classified_count} 个维度已分类"
        )
    except Exception as e:
        progress_store[task_id].update(status="error", progress=0, message=str(e))


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


def _load_active_rules() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM classification_rules WHERE is_active = 1").fetchall()
    return [dict(r) for r in rows]


@router.get("/tree/all")
async def get_tree(request: Request):
    """返回分类树数据"""
    with get_db() as conn:
        dims = [
            {"key": "dim1_hierarchy", "label": "规范层级", "field": "dim1_hierarchy"},
            {"key": "dim1_nature", "label": "规范性质", "field": "dim1_nature"},
            {"key": "dim2_stage", "label": "工程阶段", "field": "dim2_stage"},
            {"key": "dim4_specialty", "label": "所属专业", "field": "dim4_specialty"},
            {"key": "dim5_location", "label": "工程部位", "field": "dim5_location"},
            {"key": "dim6_material", "label": "材料/工艺", "field": "dim6_material"},
        ]
        result = []
        for dim in dims:
            nodes = []
            if dim["field"].startswith("dim"):
                # 条文级维度，查 clauses 表
                rows = conn.execute(
                    f"SELECT {dim['field']}, COUNT(*) as cnt FROM clauses "
                    f"WHERE {dim['field']} IS NOT NULL AND {dim['field']} != '' "
                    f"GROUP BY {dim['field']} ORDER BY cnt DESC LIMIT 30"
                ).fetchall()
            else:
                # 规范级维度，查 specifications 表
                rows = conn.execute(
                    f"SELECT {dim['field']}, COUNT(*) as cnt FROM specifications "
                    f"WHERE {dim['field']} IS NOT NULL AND {dim['field']} != '' "
                    f"GROUP BY {dim['field']} ORDER BY cnt DESC LIMIT 30"
                ).fetchall()
            for r in rows:
                val = r[dim["field"]]
                # Handle comma-separated values
                if val and "," in val:
                    for sub in val.split(","):
                        sub = sub.strip()
                        if sub:
                            nodes.append({"label": sub, "value": sub, "count": r["cnt"], "children": []})
                else:
                    nodes.append({"label": val or "(未分类)", "value": val, "count": r["cnt"], "children": []})
            result.append({"key": dim["key"], "label": dim["label"], "nodes": nodes})
        return result
