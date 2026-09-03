"""日志管理路由（spec §7.2 / §7.3）

- GET  /maintenance/logs           日志列表（分类/等级/操作者/时间范围/关键词 + 分页）
- GET  /maintenance/logs/export    导出（默认仅异常 ERROR+WARN，可覆盖）
- GET  /maintenance/qa-logs        QA 日志只读列表（qa_request_logs，分页）
- POST /maintenance/logs/clean     手动清理（scope: all/older/category，清理后写审计）
"""
import json
import logging
import time
from datetime import date, timedelta
from urllib.parse import urlencode
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from app.database import get_db
from app.logging_util import log_action, json_detail
from app.maintenance.log_cleanup import manual_clean

logger = logging.getLogger(__name__)
router = APIRouter()

LEVEL_ALLOWED = {"INFO", "WARN", "ERROR"}
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100
PRETTY_DETAIL_MAX = 2000  # 详情行内展开展示的字符上限


def _parse_filters(category: str = "", level: str = "", user: str = "",
                   start: str = "", end: str = "",
                   keyword: str = "") -> tuple[str, list]:
    """把筛选参数解析为 (WHERE 子句, 参数列表)。

    level 支持逗号分隔白名单；start/end 为 YYYY-MM-DD（非法日期忽略）；keyword 命中
    action/detail/username 任一。列表与导出共用，保证「导出当前筛选结果」一致。
    """
    conds: list[str] = []
    params: list = []
    if category:
        conds.append("category = ?")
        params.append(category)
    levels = [lv.strip().upper() for lv in (level or "").split(",")
              if lv.strip().upper() in LEVEL_ALLOWED]
    if levels:
        conds.append(f"level IN ({','.join('?' * len(levels))})")
        params.extend(levels)
    if user:
        conds.append("username = ?")
        params.append(user)
    try:
        if start:
            d0 = date.fromisoformat(start.strip())
            conds.append("created_at >= ?")
            params.append(f"{d0.isoformat()} 00:00:00")
        if end:
            d1 = date.fromisoformat(end.strip())
            conds.append("created_at < ?")
            params.append(f"{(d1 + timedelta(days=1)).isoformat()} 00:00:00")
    except ValueError:
        pass  # 非法日期范围忽略，避免 422
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        conds.append("(action LIKE ? OR detail LIKE ? OR username LIKE ?)")
        params.extend([kw, kw, kw])
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def _pretty_detail(detail) -> str:
    """detail JSON 格式化展示；非法 JSON 原样返回；统一截断上限"""
    if not detail:
        return ""
    try:
        s = json.dumps(json.loads(detail), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        s = str(detail)
    return s[:PRETTY_DETAIL_MAX]


def _qs(filters: dict, **extra) -> str:
    """拼分页链接查询串（合并当前筛选参数 + page/page_size）"""
    q = dict(filters)
    q.update(extra)
    return urlencode(q)


@router.get("/maintenance/logs")
async def logs_list(request: Request, category: str = "", level: str = "",
                    user: str = "", start: str = "", end: str = "",
                    keyword: str = "", page: int = 1,
                    page_size: int = PAGE_SIZE_DEFAULT):
    """日志列表 fragment（筛选 + 分页），供 #logs-list 容器 innerHTML 局部刷新"""
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)
    page = max(page, 1)
    where, params = _parse_filters(category, level, user, start, end, keyword)
    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM system_logs{where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM system_logs{where} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        categories = [r["category"] for r in conn.execute(
            "SELECT DISTINCT category FROM system_logs ORDER BY category").fetchall()]
    page_count = max((total + page_size - 1) // page_size, 1)
    data = []
    for r in rows:
        d = dict(r)
        d["pretty_detail"] = _pretty_detail(r["detail"])
        data.append(d)
    filters = {"category": category, "level": level, "user": user,
               "start": start, "end": end, "keyword": keyword}
    from app.main import templates
    return templates.TemplateResponse(request, "partials/logs_table.html", {
        "rows": data, "total": total, "page": page, "page_size": page_size,
        "page_count": page_count, "categories": categories,
        "prev_qs": _qs(filters, page=page - 1, page_size=page_size),
        "next_qs": _qs(filters, page=page + 1, page_size=page_size),
    })


@router.get("/maintenance/logs/export")
async def logs_export(request: Request, category: str = "", level: str = "",
                      user: str = "", start: str = "", end: str = "",
                      keyword: str = ""):
    """导出日志 JSON 附件。level 未指定时默认导出异常（ERROR+WARN）。"""
    levels = level or "ERROR,WARN"
    where, params = _parse_filters(category, levels, user, start, end, keyword)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM system_logs{where} ORDER BY created_at DESC, id DESC",
            params,
        ).fetchall()
    data = [dict(r) for r in rows]
    log_action("maintenance", "INFO", "导出异常日志", detail=str(len(data)),
               username=getattr(request.state, "username", ""))
    ts = time.strftime("%Y%m%d_%H%M%S")
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="system_logs_{ts}.json"'
    })


@router.get("/maintenance/logs/operators")
async def logs_operators(request: Request):
    """操作者候选项：system_logs 中出现过的非空 username 去重（供筛选下拉）"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT username FROM system_logs "
            "WHERE username IS NOT NULL AND username != '' ORDER BY username"
        ).fetchall()
    return [r["username"] for r in rows]


@router.get("/maintenance/qa-logs")
async def qa_logs(request: Request, page: int = 1,
                  page_size: int = PAGE_SIZE_DEFAULT):
    """QA 日志只读列表（qa_request_logs 独立存储，仅展示关键列）"""
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)
    page = max(page, 1)
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM qa_request_logs").fetchone()["c"]
        rows = conn.execute(
            """SELECT id, question, mode, backend, rerank_used, duration_ms,
                      created_at, high_count, low_count, context_tokens, context_empty
               FROM qa_request_logs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    page_count = max((total + page_size - 1) // page_size, 1)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/qa_logs_table.html", {
        "rows": [dict(r) for r in rows], "total": total,
        "page": page, "page_size": page_size, "page_count": page_count,
    })


@router.post("/maintenance/logs/clean")
async def logs_clean(request: Request, scope: str = Form(""),
                     category: str = Form("")):
    """手动清理日志（all/older/category）。清理后写 system 审计行（保留在删除之后）。"""
    if scope not in ("all", "older", "category"):
        return JSONResponse({"detail": "非法范围"}, status_code=400)
    try:
        n = manual_clean(scope, category)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    log_action("system", "INFO", "手动清理日志",
               detail=json_detail({"scope": scope, "category": category, "deleted": n}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse(f'<p style="color:green;margin-top:0.5rem">✅ 已清理 {n} 条日志</p>')
