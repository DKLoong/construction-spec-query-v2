import hashlib
import re
import uuid
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from app.config import UPLOAD_DIR, OUTPUT_DIR
from app.database import get_db
from app.logging_util import log_action, json_detail
from app.parser.md_parser import parse_markdown, is_cover_clause
from app.parser.ocr_clean import clean_ocr_text
from app.parser.spec_prefix import (
    detect_hierarchy, detect_nature, detect_industry, normalize_spec_code, PREFIX_WHITELIST,
)
from app.ocr.pdf_extract import extract_text, is_scanned
from app.classifier.rule_engine import classify_clause, should_use_ai
from app.classifier.batch_queue import try_enqueue
from app.search.vector_search import VectorStore
from app.search.embed_text import build_embed_text
from app.routes.spec_routes import SPEC_STATUS_ALLOWED

router = APIRouter()
progress_store = {}

# replaced_by_code 长度上限（防超长输入污染 DB / 前端渲染）
REPLACED_BY_CODE_MAX_LEN = 100

# 向量索引分批写入的批大小：兼顾「批量语义」（勿退回逐条 add）与「写入进度可观测」
VECTOR_WRITE_BATCH = 500


def _sanitize_status(status: str) -> str:
    """status 白名单校验（单一来源 SPEC_STATUS_ALLOWED）：非法值回退默认「现行」。

    非法 status 直接入库会导致检索默认「仅现行」下该规范静默消失。
    """
    return status if status in SPEC_STATUS_ALLOWED else "现行"


def _sanitize_replaced_by_code(replaced_by_code: str) -> str:
    """replaced_by_code 长度上限截断"""
    return (replaced_by_code or "")[:REPLACED_BY_CODE_MAX_LEN]


def _link_replacement(conn, spec_id: int, replaced_by_code: str, status: str) -> None:
    """把校核出的「替代关系编号」落到本规范上，方向**按本规范状态推断**。

    为什么必须推断而不是照抄：同一个 replaced_by_code 字段，prompt 写「被替代的规范
    编号（废止/修订中时若有）」、导入界面标签写「被替代编号」、而本函数的历史实现拿它去
    标记**库中该编号**的规范为废止（当成「我替代掉的旧规范」）。前两者读作「替代我的
    新规范」，第三者读作「我替代的旧规范」——方向相反。AI 的输出方向也随之不稳
    （文档写「本标准代替 X」→ 填旧的；写「本标准被 X 代替」→ 填新的）。

    故按下表判定（两种 AI 输出都能落到正确方向）：

    | 本规范状态   | 编号含义             | 动作                                                     |
    |-------------|---------------------|----------------------------------------------------------|
    | 废止/修订中 | 替代**它**的新规范   | 存入 replaced_by_code；库中有该新版则把自己的外键指向它     |
    | 现行        | 被**它**替代的旧规范 | 把库中那本标为废止，外键指向本规范（历史行为，保持不变）     |
    """
    if not replaced_by_code:
        return
    norm = normalize_spec_code(replaced_by_code)
    # 排除自引用：同码重导时 code = norm 会命中刚 INSERT 的行自身
    other = conn.execute(
        "SELECT id FROM specifications WHERE code = ? AND id != ?", (norm, spec_id)
    ).fetchone()

    if status in ("废止", "修订中"):
        # 本规范被替代：留存替代者编号（即便新版尚未入库，详情页也能给出可查的编号）
        conn.execute(
            "UPDATE specifications SET replaced_by_code = ? WHERE id = ?",
            (replaced_by_code, spec_id),
        )
        if other:
            conn.execute(
                "UPDATE specifications SET replace_by_spec_id = ? WHERE id = ?",
                (other["id"], spec_id),
            )
    elif other:
        # 本规范替代了旧规范：标记旧规范废止并反向关联（新规范状态不动）
        conn.execute(
            "UPDATE specifications SET status = '废止', replace_by_spec_id = ? WHERE id = ?",
            (spec_id, other["id"]),
        )


def _compute_file_hash(file_bytes: bytes) -> str:
    """计算文件的 SHA256 哈希值"""
    return hashlib.sha256(file_bytes).hexdigest()


def _parse_filename_to_code_title(filename: str) -> tuple[str, str, bool]:
    """从文件名识别规范编号与名称，返回 (code, title, matched)。

    通用命名格式：{字母前缀}[/T] {标准号}[-年份] {名称}
    例如 'GB/T 50010-2010 混凝土结构设计规范.pdf'
        → code='GB/T 50010-2010', title='混凝土结构设计规范'
    不匹配通用格式时 matched=False，交由用户手动录入。
    """
    raw = filename.strip()
    # 剥离扩展名：用 rsplit 而非 Path——文件名含 '/T'（如 GB/T 50010-2010）时，
    # '/' 会被 Path 当作路径分隔符，把 GB 误当目录吞掉。
    name = raw.rsplit(".", 1)[0].strip() if "." in raw else raw
    m = re.match(
        r"^([A-Za-z]{1,5})(/T)?[\s\-—–_]*(\d{2,5}(?:\.\d+)?)[\s\-—–_]*(\d{4})?[\s_\-—–]*(.*)$",
        name,
    )
    if not m:
        return "", "", False
    prefix, slash_t, number, year, title = m.groups()
    prefix = prefix.upper()
    if prefix not in PREFIX_WHITELIST:
        return "", "", False

    title = title.strip("- _—–.（）()　").strip()
    # 名称以残留分隔符开头（如 DB13/T 被误解析成 DB+13 后名称以 /T 开头）→ 判定不匹配
    if title and title[:1] in "/-_—–":
        return "", "", False
    # 既无名称也无年份，信息过少，不自动填充
    if not title and not year:
        return "", "", False

    code = f"{prefix}{slash_t or ''} {number}" + (f"-{year}" if year else "")
    return code, title, True


@router.post("/import/parse-filename")
async def parse_filename(filename: str = Form("")):
    """根据文件名自动识别规范编号与名称（供导入表单自动回填）"""
    code, title, matched = _parse_filename_to_code_title(filename)
    return {"code": code, "title": title, "matched": matched}


@router.post("/import/validate-version")
async def validate_version(request: Request, body: dict):
    """AI 校核规范版本与命名：返回 {status, replaced_by_code, corrected_code, corrected_title, ai_available}

    AI 不可用/异常/输出非 JSON → 兜底返回 status=现行、ai_available=False（不抛错）。
    corrected_code 一律再过 normalize_spec_code 兜底（D19 第一级正则）。
    埋点：AI 成功 INFO、降级兜底 WARN；code 为空不触发校验，不埋。
    """
    from app.ai.prompts import build_version_check_prompt
    from app.ai.cli_client import get_backend

    code = (body.get("code") or "").strip()
    title = (body.get("title") or "").strip()
    fallback = {
        "status": "现行", "replaced_by_code": "", "ai_available": False,
        "corrected_code": normalize_spec_code(code), "corrected_title": title,
    }
    if not code:
        return fallback

    username = getattr(request.state, "username", "")
    result = fallback
    ai_available = False
    try:
        backend = get_backend()
        if backend.is_available():
            import json as _json
            from app.config import WORKSPACE_DIR
            prompt = build_version_check_prompt(code, title)
            resp = await backend.ask(prompt, context="", system_prompt="", work_dir=WORKSPACE_DIR)
            if resp.success and resp.content.strip():
                parsed = _json.loads(resp.content)
                result = {
                    "status": parsed.get("status", "现行") if parsed.get("status") in ("现行", "废止", "修订中") else "现行",
                    "replaced_by_code": (parsed.get("replaced_by_code") or "").strip(),
                    "corrected_code": normalize_spec_code(parsed.get("corrected_code") or code),
                    "corrected_title": (parsed.get("corrected_title") or title).strip(),
                    "ai_available": True,
                }
                ai_available = True
    except Exception:
        pass
    if ai_available:
        log_action("import", "INFO", "版本校验完成",
                   detail=json_detail({"code": code, "title": title,
                                       "status": result["status"]}),
                   username=username)
    else:
        log_action("import", "WARN", "版本校验降级(AI不可用)",
                   detail=json_detail({"code": code, "title": title}),
                   username=username)
    return result


@router.post("/import/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    code: str = Form(""),
    force_ocr: bool = Form(False),
    status: str = Form("现行"),
    replaced_by_code: str = Form(""),
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
        log_action("import", "WARN", "导入重复文件",
                   detail=json_detail({"code": existing["code"],
                                       "title": existing["title"],
                                       "created_at": existing["created_at"],
                                       "file_hash": file_hash}),
                   username=getattr(request.state, "username", ""))
        return HTMLResponse(
            f"""<div id="import-status" style="color:#c08552;font-weight:bold">
            ⚠️ 该文件已导入过<br>
            <small>规范编号：{existing['code']} | 名称：{existing['title']}<br>
            导入时间：{existing['created_at']}</small><br>
            <a href="/specs">前往规范管理 →</a>
            </div>"""
        )

    task_id = uuid.uuid4().hex[:8]
    # 记录属主：取消/确认仅限本人（防御越权删除他人任务）
    progress_store[task_id] = {
        "status": "uploading", "progress": 0, "message": "正在上传...",
        "owner": getattr(request.state, "username", ""),
    }

    # filename 为库声明中的可选字段（`str | None`）：经 HTTP 由 Starlette 的
    # MultiPartParser 构造时恒为 str（无 filename= 的 part 会被当成普通表单字段而
    # 非 UploadFile），但直接依赖库声明之外的形态不严谨 —— 按全局规则 1.1 校验外部输入。
    if not file.filename:
        progress_store.pop(task_id, None)
        return HTMLResponse(
            '<div id="import-status" style="color:#c00;font-weight:bold">'
            "❌ 缺少文件名，无法识别文件类型</div>", status_code=400)

    ext = Path(file.filename).suffix.lower()
    save_path = Path(UPLOAD_DIR) / f"{task_id}{ext}"
    save_path.write_bytes(content)

    background_tasks.add_task(
        _process_import, task_id, str(save_path), title, code, file_hash, force_ocr,
        status, replaced_by_code,
    )
    log_action("import", "INFO", "提交导入任务",
               detail=json_detail({"task_id": task_id, "code": code, "title": title,
                                   "filename": file.filename, "status": status,
                                   "replaced_by_code": replaced_by_code}),
               username=getattr(request.state, "username", ""))
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
                    file_hash: str = "", force_ocr: bool = False,
                    status: str = "现行", replaced_by_code: str = ""):
    """后台任务 Phase 1：OCR(如需) → 暂停等待审查 → 审查后继续 Phase 2

    force_ocr=True 时强制走 OCR（适用于「半扫描」PDF：有少量文本层但格式
    会丢失，is_scanned 自动判定不可靠）。
    """
    # 入口白名单校验（status 非法回退默认「现行」；replaced_by_code 截断）
    status = _sanitize_status(status)
    replaced_by_code = _sanitize_replaced_by_code(replaced_by_code)
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
            # 保守清洗（删除页码行/纯数字行/OCR失败标记/重复页眉），再进入 Phase 2
            md_text = clean_ocr_text(md_text)
            # MD 文件直接继续 Phase 2（同一线程内安全）
            _process_import_phase2(task_id, md_text, title, code, file_path, file_hash, status, replaced_by_code)
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
                # 透传 OCR 等待/重试提示到进度 UI（如「队列繁忙，正在自动重试…」）
                if hasattr(api, "progress_cb"):
                    def _ocr_progress(msg: str, _tid: str = task_id) -> None:
                        progress_store[_tid].update(message=msg)
                    # 动态可选属性（仅 PaddleVLClient 支持）→ 用 setattr，
                    # 而非在 OCRClient 协议里声明成所有实现者的硬要求
                    setattr(api, "progress_cb", _ocr_progress)
                md_path = api.ocr_pdf_to_md(file_path)
                md_text = Path(md_path).read_text(encoding="utf-8")
            else:
                md_text = extract_text(file_path)
        else:
            progress_store[task_id].update(status="error", message=f"不支持的文件格式: {ext}")
            return

        # 保守清洗（OCR/extract 通用）：删除页码行、纯数字行、OCR 失败标记、重复页眉
        md_text = clean_ocr_text(md_text)

        # PDF 文件：保存 OCR/extract 结果，暂停等待人工审查
        progress_store[task_id]["md_text"] = md_text
        progress_store[task_id]["title"] = title
        progress_store[task_id]["code"] = code
        progress_store[task_id]["file_path"] = file_path
        progress_store[task_id]["file_name"] = path.name
        progress_store[task_id]["file_hash"] = file_hash
        # 表单传入的规范状态/被替代编号（用 spec_status 键，避免与任务处理状态 status 冲突）
        progress_store[task_id]["spec_status"] = status
        progress_store[task_id]["replaced_by_code"] = replaced_by_code

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


def _copy_ocr_images(ocr_dir: str | Path, out_dir: str | Path) -> None:
    """把 OCR 阶段下载的图片从 ocr_dir/imgs/ 复制到 out_dir/imgs/

    OCR 图片下载在 OUTPUT_DIR/{task_id}/imgs/（task_id 目录），而 spec.output_dir
    是 OUTPUT_DIR/{code}/（code 非空时目录不同）。导入确认时复制图片，使条文
    content 里的相对引用 imgs/xxx.jpg 在 spec.output_dir 下成立（供条文页展示）。
    """
    import shutil

    ocr_dir = Path(ocr_dir)
    out_dir = Path(out_dir)
    if ocr_dir == out_dir:
        return  # 未填 code 时 output_dir 即 OCR 目录，无需复制
    src = ocr_dir / "imgs"
    if not src.is_dir():
        return
    shutil.copytree(src, out_dir / "imgs", dirs_exist_ok=True)


def _filter_cover_clauses(clauses: list[dict]) -> list[dict]:
    """过滤封面/出版信息页脏数据条文（命中封面特征词 ≥2 个直接丢弃）

    返回过滤后的条文列表，不 INSERT 封面脏数据（如标准首页、出版信息页）。
    """
    return [c for c in clauses if not is_cover_clause(c.get("content", ""))]


def _process_import_phase2(task_id: str, md_text: str, title: str, code: str,
                            file_path: str, file_hash: str = "",
                            status: str = "现行", replaced_by_code: str = ""):
    """后台任务 Phase 2：解析 → 分类 → 索引（始终创建新连接，线程安全）"""
    # 入库前白名单兜底校验（覆盖 upload_file → _process_import 与 confirm_review 两条路径）
    status = _sanitize_status(status)
    replaced_by_code = _sanitize_replaced_by_code(replaced_by_code)
    conn = None
    try:
        from app.database import get_connection
        conn = get_connection()

        progress_store[task_id].update(progress=60, message="正在解析条文...")

        # Step 2: 解析条文 + 过滤封面/出版信息页脏数据
        clauses_data = _filter_cover_clauses(parse_markdown(md_text))

        # Step 3: 规范级分类（code 先归一化，再 detect 层级/性质/行业）
        code = normalize_spec_code(code) or Path(file_path).stem
        dim1_hierarchy = detect_hierarchy(code)
        dim1_nature = detect_nature(code)
        dim1_industry = detect_industry(code)

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

        # 复制 OCR 图片到 spec.output_dir（图片实际下载在 task_id 目录）
        _copy_ocr_images(Path(OUTPUT_DIR) / task_id, output_dir)

        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature,
               dim1_industry, dim2_stage, dim3_usage, source_path, output_dir, file_hash, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code or Path(file_path).stem, title or Path(file_path).stem,
             dim1_hierarchy, dim1_nature, dim1_industry,
             dim2_stage, dim3_usage,
             file_path, output_dir, file_hash, status),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 替代关系落库（方向按本规范状态推断，见 _link_replacement 文档）
        _link_replacement(conn, spec_id, replaced_by_code, status)

        classified_count = 0
        # 向量阶段的失败信息先收集、commit 后再落日志：log_action 自开新连接，
        # 在本函数未提交的事务内调用会 BUSY 并被其静默丢弃（同下方「导入成功」埋点注释）。
        vector_warn = None
        vs = None
        try:
            vs = VectorStore()
        except Exception as e:
            # 绝不静默：vs 为 None 意味着本批条文的向量一条都不会写，
            # 事后唯一症状是维护页「缺失向量索引」而无从追查原因。
            vector_warn = f"向量库不可用，本批条文未写向量索引: {e}"

        # 第一步：先插入所有条文到 SQLite，收集需要 embedding 的记录
        # 整批只取一次 AI 介入阈值（DB 覆盖热生效），避免条文循环内反复查
        from app.params.registry import get_adaptive_thresholds
        adaptive_thresholds = get_adaptive_thresholds()
        embedding_records = []
        for cd in clauses_data:
            scores, best_labels, best_rule_ids = classify_clause(cd["content"], cd.get("parent_path", []), rules)
            dim4_val = best_labels.get("dim4", "")
            dim5_val = best_labels.get("dim5", "")
            dim6_val = best_labels.get("dim6", "")

            from app.search.tokenize import build_search_text
            conn.execute(
                """INSERT INTO clauses (spec_id, clause_no, title, content, parent_clause,
                   dim4_specialty, dim5_location, dim6_material, clause_is_non, search_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (spec_id, cd["clause_no"], cd["title"], cd["content"], None,
                 dim4_val, dim5_val, dim6_val,
                 1 if cd.get("is_non_clause") else 0,
                 build_search_text(cd["clause_no"], cd["title"], cd["content"])),
            )
            clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for dim in ["dim4", "dim5", "dim6"]:
                rule_id = best_rule_ids.get(dim)
                if should_use_ai(dim, scores, adaptive_thresholds):
                    # 入列自检：已删除条文/废止规范/非条文不入队；同(clause,dim)防重
                    try_enqueue(conn, clause_id, dim, scores[dim])
                    # 规则匹配到但得分不足 → 仅记录命中（统计语义与旧实现一致）
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
                embed_text = build_embed_text(code, title, cd["clause_no"], cd["title"], cd["content"])
                embedding_records.append({
                    "clause_id": clause_id,
                    "spec_id": spec_id,
                    "text": embed_text,
                    "dim_scores": dim_scores_str,
                })

        # 第二步：批量计算 embedding（比逐条快一个数量级）
        if vs is not None and embedding_records:
            try:
                from app.ai.embedding import embed_texts, get_model
                import numpy as np

                n_vec = len(embedding_records)
                # 模型首次加载约 30 秒，是本流程最长的单点停顿。若与编码合并为一步，
                # 进度条会静止半分钟——单列一档，用户才知道在等什么。
                progress_store[task_id].update(
                    progress=80, message="正在加载向量模型（首次约 30 秒）…")
                if get_model() is None:
                    raise RuntimeError("Embedding 模型不可用")

                progress_store[task_id].update(
                    progress=84, message=f"正在生成向量（{n_vec} 条）…")
                texts = [r["text"] for r in embedding_records]
                embeddings = embed_texts(texts)

                records = [
                    {
                        "clause_id": r["clause_id"],
                        "spec_id": r["spec_id"],
                        "text": r["text"],
                        "embedding": np.array(embeddings[i], dtype=np.float32),
                        "dim_scores": r["dim_scores"],
                        "chunk_index": 0,
                    }
                    for i, r in enumerate(embedding_records)
                ]

                # 表不存在时先按显式 schema 建表，确保 embedding 列是固定大小向量类型
                if not vs._table_exists():
                    first_emb = np.array(embeddings[0], dtype=np.float32)
                    from app.search.vector_search import embedding_schema
                    vs.db.create_table("clause_embeddings",
                                       schema=embedding_schema(len(first_emb)))

                # 分批写入。两条约束同时成立：
                # 1) 不得退回逐条 add——实测逐条（且每行重开表）比批量慢 40 倍，
                #    且每次 add 产生一个新版本（真实表曾落到 rows=73 / version=173）
                # 2) 不得单次 add 全量——上万条时写入期间进度完全不动
                # 回归测试：tests/test_import_vector_batch.py
                total = len(records)
                for start in range(0, total, VECTOR_WRITE_BATCH):
                    vs._get_table().add(records[start:start + VECTOR_WRITE_BATCH])
                    done = min(start + VECTOR_WRITE_BATCH, total)
                    progress_store[task_id].update(
                        progress=90 + int(9 * done / total),
                        message=f"正在写入向量索引（{done}/{total}）…")
            except Exception as e:
                # 不影响导入完成，但绝不静默：进度 message 随任务结束即消失，
                # 故同时收集告警，commit 后落 system_logs（否则向量缺失无从追查）。
                # 只改文案、不动 progress——此处进度可能已推进到 90+，写回固定值
                # 会造成进度回退，反而更像故障。
                progress_store[task_id].update(
                    message=f"向量索引部分失败: {str(e)}"
                )
                vector_warn = f"向量索引写入失败，部分条文缺索引: {e}"

        conn.execute("UPDATE specifications SET clause_count = ? WHERE id = ?",
                    (len(clauses_data), spec_id))
        conn.commit()
        # commit 之后再埋点（log_action 自开新连接，事务内调用会 BUSY）
        log_action("import", "INFO", "导入成功",
                   detail=json_detail({"spec_id": spec_id, "code": code,
                                       "clause_count": len(clauses_data),
                                       "replaced_by_code": replaced_by_code}),
                   username=progress_store.get(task_id, {}).get("owner", "system"))
        if vector_warn:
            # 向量阶段失败在此统一落 WARN：条文已入库但索引不全，
            # 维护页会显示「缺失向量索引」，这条日志是唯一的追查线索
            log_action("import", "WARN", "向量索引未完整写入",
                       detail=json_detail({"code": code, "reason": vector_warn,
                                           "clause_count": len(embedding_records)}),
                       username=progress_store.get(task_id, {}).get("owner", "system"))

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
        log_action("import", "ERROR", "导入失败",
                   detail=json_detail({"task_id": task_id, "code": code, "error": str(e)}),
                   username=progress_store.get(task_id, {}).get("owner", "system"))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


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
        # 审查页隐藏左侧分类树，页面两栏全宽显示（编辑 | 预览）
        "hide_tree": True,
    })


@router.get("/import/review/{task_id}/content")
async def review_content(request: Request, task_id: str):
    """获取 OCR 原始文本"""
    task = progress_store.get(task_id)
    if not task or "md_text" not in task:
        return JSONResponse({"detail": "任务不存在或已过期"}, status_code=404)
    return {"content": task["md_text"], "file_name": task.get("file_name", "")}


@router.get("/import/review/{task_id}/imgs/{filename}")
async def review_image(request: Request, task_id: str, filename: str):
    """服务 OCR 审查页 markdown 中引用的图片

    OCR 阶段已把官网 markdown.images 的图片按相对路径 imgs/xxx.jpg 保存到
    OUTPUT_DIR/{task_id}/imgs/；审查页前端把相对引用改写为
    /import/review/{task_id}/imgs/{filename} 后由本路由返回文件。
    """
    from fastapi.responses import FileResponse

    # task_id 为 uuid4().hex[:8]（8 位十六进制），校验防止目录拼接越权
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)
    # 仅取文件名，防路径穿越
    safe_name = Path(filename).name
    img_path = Path(OUTPUT_DIR) / task_id / "imgs" / safe_name
    if not img_path.is_file():
        return JSONResponse({"detail": "图片不存在"}, status_code=404)
    return FileResponse(str(img_path))


@router.post("/import/review/{task_id}/confirm")
async def confirm_review(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
):
    """审查确认：提交修改后的 Markdown 文本（raw body），继续 Phase 2

    **md 走 raw body，不走表单字段**：Starlette 对表单字段有 1MB 硬上限
    （`MultiPartParser.max_part_size`，urlencoded 同样受管），超限时在**路由体执行
    之前**就抛 400，导致既无确认日志也无成功/失败日志、Phase 2 从未启动；而 HTMX
    对 4xx 默认不 swap，页面毫无反应。实测 CJJ 2-2008 的 md 46 万字符编码后 1.52MB
    即触发（JGJ 107-2016 的 5.7 万字符则正常），是「大规范根本导不进来」的堵点。
    raw body 无此限制。回归测试见 tests/test_confirm_transport.py。
    """
    task = progress_store.get(task_id)
    if not task:
        return HTMLResponse("<p style='color:red'>任务不存在或已过期</p>")

    # 防止重复点击确认按钮
    if task.get("status") in ("processing", "done"):
        return HTMLResponse(
            f"""<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">
            <p style="color:#c08552">⏳ 导入正在处理中，请勿重复提交...</p></div>"""
        )

    # 读取 raw body 并按 UTF-8 解码（外部输入校验：编码非法/内容为空一律拒绝，
    # 且必须在改动任务状态之前返回，避免"没提交内容却已置为 processing"）
    try:
        content = (await request.body()).decode("utf-8")
    except UnicodeDecodeError:
        return HTMLResponse(
            "<p style='color:red'>审查内容编码非法（需 UTF-8），未提交</p>",
            status_code=400,
        )
    if not content.strip():
        return HTMLResponse(
            "<p style='color:red'>审查内容为空，未提交</p>", status_code=400
        )

    title = task.get("title", "")
    code = task.get("code", "")
    file_path = task.get("file_path", "")
    file_hash = task.get("file_hash", "")
    status = task.get("spec_status", "现行")
    replaced_by_code = task.get("replaced_by_code", "")

    # 更新为审查后的文本
    task["md_text"] = content
    task.update(status="processing", progress=55, message="审查完成，正在继续导入...")

    # 启动 Phase 2（不再跨线程传递 conn，Phase 2 自己创建连接）
    background_tasks.add_task(
        _process_import_phase2, task_id, content, title, code, file_path, file_hash,
        status, replaced_by_code,
    )
    log_action("import", "INFO", "审查确认继续导入",
               detail=json_detail({"task_id": task_id, "code": code}),
               username=task.get("owner") or getattr(request.state, "username", ""))

    return HTMLResponse(
        f"""<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">
        <p>审查完成，正在继续导入...</p></div>"""
    )


@router.post("/import/review/{task_id}/cancel")
async def cancel_review(request: Request, task_id: str):
    """取消审查：清理磁盘残留并移除任务，返回主界面

    清理 uploads/{task_id}.{ext}（原始上传文件）与 outputs/{task_id}/（OCR 结果
    目录，含 imgs/ 已下载图片）。任务不存在时仍执行磁盘清理——服务重启后
    progress_store 被清空但磁盘残留仍在，幂等清理兜底；但被 specifications
    引用的路径一律不删（防误删已入库规范数据）。
    """
    # 校验 task_id 为 uuid4().hex[:8] 格式，防止目录拼接越权
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)

    task = progress_store.get(task_id)
    username = getattr(request.state, "username", "")

    if task is not None:
        # 属主校验：仅任务属主可取消（旧任务无 owner 字段视为可取消，兼容历史）
        if task.get("owner") and task.get("owner") != username:
            return JSONResponse({"detail": "无权取消他人任务"}, status_code=403)
        # 已完成导入不可取消（避免删除已入库规范的源文件/输出目录）
        if task.get("status") == "done":
            return JSONResponse({"detail": "导入已完成，无法取消"}, status_code=409)

    _cleanup_task_artifacts(task_id)

    # 移除内存任务（幂等：不存在也无妨）
    progress_store.pop(task_id, None)
    log_action("import", "INFO", "取消导入审查",
               detail=json_detail({"task_id": task_id}),
               username=username)

    # HX-Redirect 让 HTMX 整页跳回主界面
    return HTMLResponse("", headers={"HX-Redirect": "/"})


def _cleanup_task_artifacts(task_id: str) -> None:
    """清理任务残留（uploads/{task_id}.* 与 outputs/{task_id}/）

    被 specifications 引用的路径不删：output_dir 或 source_path 指向该路径时，
    说明是已入库规范的数据，跳过删除（防御与持久目录的命名空间冲突）。
    """
    import shutil

    with get_db() as conn:
        ref_outputs = {str(r["output_dir"]).replace("\\", "/") for r in conn.execute(
            "SELECT output_dir FROM specifications WHERE output_dir IS NOT NULL")}
        ref_uploads = {Path(r["source_path"]).name for r in conn.execute(
            "SELECT source_path FROM specifications WHERE source_path IS NOT NULL")}

    # 清理 OCR 结果目录 outputs/{task_id}/
    out_dir = Path(OUTPUT_DIR) / task_id
    key = str(out_dir).replace("\\", "/")
    if out_dir.is_dir() and key not in ref_outputs:
        shutil.rmtree(out_dir, ignore_errors=True)

    # 清理上传文件 uploads/{task_id}.{ext}
    for p in Path(UPLOAD_DIR).glob(f"{task_id}.*"):
        if p.name in ref_uploads:
            continue
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


@router.get("/tree/all")
async def get_tree(request: Request):
    """返回分类树数据"""
    # dim1/2/3 在 specifications 表，dim4/5/6 在 clauses 表
    spec_dims = [
        {"key": "dim1_hierarchy", "label": "规范层级", "field": "dim1_hierarchy"},
        {"key": "dim1_industry", "label": "规范行业", "field": "dim1_industry"},
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
