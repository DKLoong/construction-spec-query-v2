"""维护工具路由：健康检查 / 导出备份 / FTS optimize / 日志界面（P3）"""
import logging
import time
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from app.database import get_db
from app.logging_util import log_action

logger = logging.getLogger(__name__)
router = APIRouter()


def _render_health_result(request: Request, result: dict):
    from app.main import templates
    return templates.TemplateResponse(request, "partials/maintenance_health_result.html", {
        "checks": result.get("checks", []),
    })


@router.get("/maintenance")
async def maintenance_page(request: Request):
    """维护界面（三 Tab：健康检查 / 导出备份 / 日志管理-占位）"""
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/maintenance.html",
    })


@router.post("/maintenance/health-check")
async def health_check_run(request: Request):
    """运行健康检查，返回结果片段"""
    from app.maintenance.health_check import run_health_check
    start = time.time()
    result = run_health_check()
    log_action("maintenance", "INFO", "运行健康检查",
               detail=str(result), duration_ms=int((time.time() - start) * 1000))
    return _render_health_result(request, result)


@router.post("/maintenance/fix/{key}")
async def health_fix_one(request: Request, key: str):
    """单项修复后返回更新结果"""
    from app.maintenance.health_check import fix_issue, run_health_check
    fix_issue(key)
    return _render_health_result(request, run_health_check())


@router.post("/maintenance/fix-all")
async def health_fix_all(request: Request):
    """一键修复全部可修复项"""
    from app.maintenance.health_check import fix_all, run_health_check
    fix_all()
    return _render_health_result(request, run_health_check())


# 后台重建进度（task_id -> {status, progress, message}），与导入进度相互独立
rebuild_progress: dict = {}


def _run_rebuild(task_id: str):
    """后台线程：清空并全量重建向量索引，逐批更新 rebuild_progress（不阻塞请求处理）"""
    try:
        rebuild_progress[task_id] = {"status": "running", "progress": 0,
                                     "message": "正在清空旧向量索引…"}
        from app.search.vector_search import VectorStore
        from app.search.embed_text import build_embed_text
        with get_db() as conn:
            clauses = conn.execute(
                """SELECT c.id, c.spec_id, c.clause_no, c.title, c.content,
                          s.code, s.title as spec_title
                   FROM clauses c JOIN specifications s ON c.spec_id = s.id
                   ORDER BY c.id"""
            ).fetchall()
        if not clauses:
            rebuild_progress[task_id] = {"status": "done", "progress": 100,
                                         "message": "无条文，无需重建"}
            log_action("maintenance", "INFO", "重建向量索引", detail="0")
            return
        records = []
        for c in clauses:
            records.append({
                "clause_id": c["id"], "spec_id": c["spec_id"],
                "text": build_embed_text(c["code"], c["spec_title"], c["clause_no"],
                                         c["title"], c["content"]),
                "dim_scores": "",
            })
        total = len(records)

        def _cb(done: int, n: int) -> None:
            pct = int(done * 100 / n) if n else 100
            rebuild_progress[task_id] = {"status": "running", "progress": pct,
                                         "message": f"已重建 {done}/{n} 条"}

        # batch_index 内部先 clear_all 再逐批写入，progress_cb 每批上报
        VectorStore().batch_index(records, progress_cb=_cb)
        rebuild_progress[task_id] = {"status": "done", "progress": 100,
                                     "message": f"重建完成，共 {total} 条"}
        log_action("maintenance", "INFO", "重建向量索引", detail=str(total))
    except Exception as e:
        logger.warning("重建向量索引失败: %s", e)
        rebuild_progress[task_id] = {"status": "error", "progress": 0, "message": str(e)}
        log_action("maintenance", "ERROR", "重建向量索引失败", detail=str(e))


@router.post("/maintenance/rebuild-vectors")
async def rebuild_vectors(request: Request):
    """启动后台全量重建向量索引，立即返回 task_id（前端轮询 rebuild-progress 展示进度）"""
    import threading
    task_id = f"rb{int(time.time() * 1000)}"
    rebuild_progress[task_id] = {"status": "pending", "progress": 0, "message": "正在启动…"}
    threading.Thread(target=_run_rebuild, args=(task_id,), daemon=True).start()
    return JSONResponse({"task_id": task_id})


@router.get("/maintenance/rebuild-progress/{task_id}")
async def rebuild_progress_endpoint(request: Request, task_id: str):
    """重建进度 JSON：{status: pending|running|done|error|unknown, progress: 0-100, message}"""
    p = rebuild_progress.get(task_id, {"status": "unknown", "progress": 0,
                                       "message": "任务不存在或已过期"})
    return JSONResponse(p)


@router.post("/maintenance/backup")
async def backup_db(request: Request):
    """SQLite 单文件备份：VACUUM INTO data/backups/spec_query_<ts>.db

    注意：VACUUM INTO 的目标路径必须是字面量（不支持 ? 参数绑定），且目标文件
    已存在时会报错。路径由 BACKUP_DIR + 服务端时间戳生成（非用户输入），单引号
    转义后安全拼接；同秒重复备份追加 _2 序号避免冲突。
    """
    import sqlite3
    from app.database import DATABASE_PATH  # database 模块路径（测试 monkeypatch 该处）
    from app.config import BACKUP_DIR
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    base = time.strftime("%Y%m%d_%H%M%S")
    target = Path(BACKUP_DIR) / f"spec_query_{base}.db"
    seq = 2
    while target.exists():
        target = Path(BACKUP_DIR) / f"spec_query_{base}_{seq}.db"
        seq += 1
    escaped = str(target).replace("'", "''")
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(f"VACUUM INTO '{escaped}'")
    finally:
        conn.close()
    log_action("maintenance", "INFO", "SQLite 备份", detail=str(target))
    return HTMLResponse(f"""<p style="color:green;margin-top:0.5rem">✅ 已备份至：<code>{target.name}</code></p>""")


@router.get("/maintenance/export/rules")
async def export_rules(request: Request):
    """导出全部分类规则 JSON（附件下载）"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM classification_rules ORDER BY dimension, id").fetchall()
    data = [dict(r) for r in rows]
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_action("maintenance", "INFO", "导出规则", detail=str(len(data)))
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="classification_rules_{ts}.json"'
    })


@router.get("/maintenance/export/review-queue")
async def export_review_queue(request: Request):
    """导出待复核队列 JSON（含条文内容/AI 标签/置信度）"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT q.id as queue_id, q.clause_id, q.dimension, q.keyword_score,
                      q.ai_label, q.ai_confidence, q.status, q.created_at,
                      c.clause_no, c.title as clause_title, c.content,
                      s.code as spec_code, s.title as spec_title
               FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               JOIN specifications s ON c.spec_id = s.id
               WHERE q.status = 'review'
               ORDER BY q.created_at DESC"""
        ).fetchall()
    data = [dict(r) for r in rows]
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_action("maintenance", "INFO", "导出复核队列", detail=str(len(data)))
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="review_queue_{ts}.json"'
    })


@router.post("/maintenance/fts-optimize")
async def fts_optimize(request: Request):
    """SQLite FTS5 定期 optimize：合并碎片，提升检索性能"""
    with get_db() as conn:
        conn.execute("INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')")
    log_action("maintenance", "INFO", "FTS optimize")
    return HTMLResponse('<p style="color:green;margin-top:0.5rem">✅ FTS5 optimize 完成</p>')
