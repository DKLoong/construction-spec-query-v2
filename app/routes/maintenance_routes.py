"""维护工具路由：健康检查 / 导出备份 / FTS optimize / 日志界面（P3）"""
import time
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.logging_util import log_action

router = APIRouter()


def _render_health_result(request: Request, result: dict):
    from app.main import templates
    return templates.TemplateResponse(request, "partials/maintenance_health_result.html", {
        "checks": result.get("checks", []),
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


@router.post("/maintenance/rebuild-vectors")
async def rebuild_vectors(request: Request):
    """全量重建向量索引（清空 + 全部条文重新 embedding）"""
    from app.database import get_db as _get_db
    from app.search.vector_search import VectorStore
    from app.search.tokenize import build_search_text  # noqa: F401

    vs = VectorStore()
    vs.clear_all()
    with _get_db() as conn:
        clauses = conn.execute(
            """SELECT c.id, c.spec_id, c.clause_no, c.title, c.content,
                      s.code, s.title as spec_title
               FROM clauses c JOIN specifications s ON c.spec_id = s.id
               ORDER BY c.id"""
        ).fetchall()
    records = []
    for c in clauses:
        embed_text = (f"{c['code'] or ''} {c['spec_title'] or ''} "
                      f"[{c['clause_no']}] {c['title'] or ''} {c['content']}")
        records.append({"clause_id": c["id"], "spec_id": c["spec_id"],
                        "text": embed_text, "dim_scores": ""})
    vs.batch_index(records)
    log_action("maintenance", "INFO", "重建向量索引", detail=str(len(records)))
    from app.main import templates
    return HTMLResponse(f"""<p style="color:green;margin-top:0.5rem">✅ 向量索引已重建：{len(records)} 条</p>
    <div hx-post="/maintenance/health-check" hx-trigger="load" hx-swap="outerHTML"></div>""")
