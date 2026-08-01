import hashlib
import re
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


def _compute_file_hash(file_bytes: bytes) -> str:
    """计算文件的 SHA256 哈希值"""
    return hashlib.sha256(file_bytes).hexdigest()


@router.post("/import/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    code: str = Form(""),
    force_ocr: bool = Form(False),
):
    content = await file.read()
    file_hash = _compute_file_hash(content)

    # 检查是否已导入过相同文件
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, code, title, created_at FROM specifications WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()

    if existing:
        return HTMLResponse(
            f"""<div id="import-status" style="color:#c08552;font-weight:bold">
            ⚠️ 该文件已导入过<br>
            <small>规范编号：{existing['code']} | 名称：{existing['title']}<br>
            导入时间：{existing['created_at']}</small><br>
            <a href="/specs">前往规范管理 →</a>
            </div>"""
        )

    task_id = uuid.uuid4().hex[:8]
    progress_store[task_id] = {"status": "uploading", "progress": 0, "message": "正在上传..."}

    ext = Path(file.filename).suffix.lower()
    save_path = Path(UPLOAD_DIR) / f"{task_id}{ext}"
    save_path.write_bytes(content)

    background_tasks.add_task(
        _process_import, task_id, str(save_path), title, code, file_hash, force_ocr
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


def _process_import(task_id: str, file_path: str, title: str, code: str,
                    file_hash: str = "", force_ocr: bool = False):
    """后台任务 Phase 1：OCR(如需) → 暂停等待审查 → 审查后继续 Phase 2

    force_ocr=True 时强制走 OCR（适用于「半扫描」PDF：有少量文本层但格式
    会丢失，is_scanned 自动判定不可靠）。
    """
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
            # MD 文件直接继续 Phase 2（同一线程内安全）
            _process_import_phase2(task_id, md_text, title, code, file_path, file_hash)
            return
        elif ext == ".pdf":
            if force_ocr or is_scanned(file_path):
                progress_store[task_id].update(progress=20, message="正在 OCR 识别...")
                from app.ocr.paddle_api import create_ocr_client
                try:
                    api = create_ocr_client()
                except RuntimeError as e:
                    progress_store[task_id].update(
                        status="error", progress=0,
                        message=str(e)
                    )
                    return
                md_path = api.ocr_pdf_to_md(file_path)
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
        progress_store[task_id]["file_hash"] = file_hash

        progress_store[task_id].update(
            status="review_needed", progress=50,
            message="OCR 完成，请审查识别结果",
        )
        return
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        progress_store[task_id].update(status="error", progress=0, message=str(e))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _process_import_phase2(task_id: str, md_text: str, title: str, code: str,
                            file_path: str, file_hash: str = ""):
    """后台任务 Phase 2：解析 → 分类 → 索引（始终创建新连接，线程安全）"""
    conn = None
    try:
        from app.database import get_connection
        conn = get_connection()

        progress_store[task_id].update(progress=60, message="正在解析条文...")

        # Step 2: 解析条文
        clauses_data = parse_markdown(md_text)

        # Step 3: 规范级分类
        dim1_hierarchy = _detect_hierarchy(code)
        dim1_nature = _detect_nature(code)

        progress_store[task_id].update(progress=70, message=f"正在分类 {len(clauses_data)} 条条文...")

        # Step 4: 加载分类规则
        rules_rows = conn.execute(
            "SELECT * FROM classification_rules WHERE is_active = 1"
        ).fetchall()
        rules = [dict(r) for r in rules_rows]

        # Step 5: 规范级分类 (dim2/dim3)
        spec_classify_text = f"{code} {title}"
        spec_scores, spec_labels, spec_rule_ids = classify_clause(spec_classify_text, [], rules)
        dim2_stage = spec_labels.get("dim2", "")
        dim3_usage = spec_labels.get("dim3", "")

        # 更新规范级规则统计
        for dim in ("dim2", "dim3"):
            rule_id = spec_rule_ids.get(dim)
            if rule_id:
                conn.execute(
                    "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                    (rule_id,),
                )

        # Step 6: 写入数据库 + 分类 + 向量索引
        output_dir = str(Path(OUTPUT_DIR) / (code or Path(file_path).stem))
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature,
               dim2_stage, dim3_usage, source_path, output_dir, file_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code or Path(file_path).stem, title or Path(file_path).stem,
             dim1_hierarchy, dim1_nature,
             dim2_stage, dim3_usage,
             file_path, output_dir, file_hash),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        classified_count = 0
        vs = None
        try:
            vs = VectorStore()
        except Exception:
            pass

        # 第一步：先插入所有条文到 SQLite，收集需要 embedding 的记录
        embedding_records = []
        for cd in clauses_data:
            scores, best_labels, best_rule_ids = classify_clause(cd["content"], cd.get("parent_path", []), rules)
            dim4_val = best_labels.get("dim4", "")
            dim5_val = best_labels.get("dim5", "")
            dim6_val = best_labels.get("dim6", "")

            conn.execute(
                """INSERT INTO clauses (spec_id, clause_no, title, content, parent_clause,
                   dim4_specialty, dim5_location, dim6_material)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (spec_id, cd["clause_no"], cd["title"], cd["content"], None,
                 dim4_val, dim5_val, dim6_val),
            )
            clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for dim in ["dim4", "dim5", "dim6"]:
                rule_id = best_rule_ids.get(dim)
                if should_use_ai(dim, scores, ADAPTIVE_THRESHOLDS):
                    conn.execute(
                        "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, ?)",
                        (clause_id, dim, scores[dim]),
                    )
                    # 规则匹配到但得分不足 → 仅记录命中
                    if rule_id:
                        conn.execute(
                            "UPDATE classification_rules SET hit_count = hit_count + 1 WHERE id = ?",
                            (rule_id,),
                        )
                elif best_labels.get(dim):
                    classified_count += 1
                    # 规则匹配到且得分达标 → 命中 + 确认
                    if rule_id:
                        conn.execute(
                            "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                            (rule_id,),
                        )

            if vs is not None:
                dim_scores_str = ",".join(f"{k}={v:.2f}" for k, v in scores.items())
                embed_text = f"{code or ''} {title or ''} [{cd['clause_no']}] {cd['title'] or ''} {cd['content']}"
                embedding_records.append({
                    "clause_id": clause_id,
                    "spec_id": spec_id,
                    "text": embed_text,
                    "dim_scores": dim_scores_str,
                })

        # 第二步：批量计算 embedding（比逐条快一个数量级）
        if vs is not None and embedding_records:
            try:
                from app.ai.embedding import embed_texts
                import numpy as np

                texts = [r["text"] for r in embedding_records]
                embeddings = embed_texts(texts)

                # 批量添加到 LanceDB
                if vs._table_exists():
                    # 表已存在，逐条添加
                    for i, r in enumerate(embedding_records):
                        emb = np.array(embeddings[i], dtype=np.float32)
                        vs._get_table().add([{
                            "clause_id": r["clause_id"],
                            "spec_id": r["spec_id"],
                            "text": r["text"],
                            "embedding": emb,
                            "dim_scores": r["dim_scores"],
                        }])
                else:
                    # 表不存在，创建 schema 并批量添加
                    first_emb = np.array(embeddings[0], dtype=np.float32)
                    import pyarrow as pa
                    schema = pa.schema([
                        pa.field("clause_id", pa.int64()),
                        pa.field("spec_id", pa.int64()),
                        pa.field("text", pa.string()),
                        pa.field("embedding", pa.list_(pa.float32(), len(first_emb))),
                        pa.field("dim_scores", pa.string()),
                    ])
                    tbl = vs.db.create_table("clause_embeddings", schema=schema)
                    records = []
                    for i, r in enumerate(embedding_records):
                        records.append({
                            "clause_id": r["clause_id"],
                            "spec_id": r["spec_id"],
                            "text": r["text"],
                            "embedding": np.array(embeddings[i], dtype=np.float32),
                            "dim_scores": r["dim_scores"],
                        })
                    tbl.add(records)
            except Exception as e:
                # 批量失败静默降级，不影响导入完成
                progress_store[task_id].update(
                    progress=85, message=f"向量索引部分失败: {str(e)}"
                )

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
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _detect_hierarchy(code: str) -> str:
    """根据规范编号前缀判断规范层级"""
    if not code:
        return ""

    # 规范化：去掉可能存在的斜杠（Windows 不允许文件名含 /）
    c = code.replace("/", "").strip()
    if not c:
        return ""

    # 前缀按长度降序排列，确保最长前缀优先匹配
    # 例如 JTG 必须在 JT 之前检查，GBT 必须在 GB 之前检查
    PREFIX_MAP = [
        ("JTGT", "公路工程"),
        ("JTG", "公路工程"),
        ("JTT", "交通运输"),
        ("JT", "交通运输"),
        ("JGT", "建筑工业"),
        ("JG", "建筑工业"),
        ("JGJ", "建筑工程"),
        ("CJT", "城镇建设"),
        ("CJ", "城镇建设"),
        ("JBT", "机械"),
        ("JB", "机械"),
        ("NYT", "农业"),
        ("NY", "农业"),
        ("GBT", "国家标准"),
        ("GB", "国家标准"),
        ("TB", "铁路"),
        ("MH", "民用航空"),
        ("YZ", "邮政"),
        ("DB", "地方标准"),
    ]

    for prefix, hierarchy in PREFIX_MAP:
        if c.startswith(prefix):
            return hierarchy

    # 单字母前缀
    if c.startswith("T"):
        return "团体标准"
    if c.startswith("Q"):
        return "企业标准"

    return ""


def _detect_nature(code: str) -> str:
    """根据规范编号判断强制性/推荐性"""
    if not code:
        return ""

    c = code.replace("/", "").strip()
    if not c:
        return ""

    # 企业标准：无强制/推荐之分
    if c.startswith("Q"):
        return ""

    # 团体标准：始终推荐性（T 后不能紧跟字母，以区分 TB）
    if re.match(r"^T($|\s|\d)", c):
        return "推荐性"

    # 邮政标准：始终推荐性
    if c.startswith("YZ"):
        return "推荐性"

    # 提取前缀字母段
    m = re.match(r"^([A-Za-z]+)", c)
    if not m:
        return "强制性"

    letters = m.group(1)

    # 已知的推荐性变体（前缀字母段精确匹配）
    RECOMMENDED = {"GBT", "JTT", "JTGT", "JGT", "CJT", "JBT", "NYT", "DBT"}
    if letters in RECOMMENDED:
        return "推荐性"

    # 原始字符串中包含 "/T" 模式（如 DB13/T 这种非规范写法）
    if "/T" in code:
        return "推荐性"

    return "强制性"


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
        })

    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/ocr_review.html",
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

    # 防止重复点击确认按钮
    if task.get("status") in ("processing", "done"):
        return HTMLResponse(
            f"""<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">
            <p style="color:#c08552">⏳ 导入正在处理中，请勿重复提交...</p></div>"""
        )

    title = task.get("title", "")
    code = task.get("code", "")
    file_path = task.get("file_path", "")
    file_hash = task.get("file_hash", "")

    # 更新为审查后的文本
    task["md_text"] = content
    task.update(status="processing", progress=55, message="审查完成，正在继续导入...")

    # 启动 Phase 2（不再跨线程传递 conn，Phase 2 自己创建连接）
    background_tasks.add_task(
        _process_import_phase2, task_id, content, title, code, file_path, file_hash
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
