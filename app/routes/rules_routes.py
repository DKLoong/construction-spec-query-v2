"""分类规则管理 + 审核队列路由"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.models import ClassificationRuleCreate

router = APIRouter()

dim_labels = {
    "dim1": "规范属性", "dim2": "工程阶段", "dim3": "工程类型",
    "dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"
}


def _render_stats_oob():
    """渲染规则统计面板 HTML（带 hx-swap-oob，供响应内联使用）"""
    from app.database import get_db
    with get_db() as conn:
        stats_rows = conn.execute(
            "SELECT dimension, COUNT(*) as cnt, "
            "SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) as active "
            "FROM classification_rules GROUP BY dimension ORDER BY dimension"
        ).fetchall()
    parts = []
    for s in stats_rows:
        label = dim_labels.get(s["dimension"], s["dimension"])
        parts.append(
            f'<small style="background:var(--pico-secondary-background);'
            f'padding:0.2rem 0.5rem;border-radius:4px">'
            f'{label}: <strong>{s["active"]}/{s["cnt"]}</strong></small>'
        )
    return (
        f'<div id="rules-stats" hx-swap-oob="true" '
        f'style="margin-bottom:0.5rem;display:flex;gap:1rem;flex-wrap:wrap"'
        f' hx-get="/rules/stats" hx-trigger="statsRefresh from:body" hx-swap="outerHTML">'
        f'{"".join(parts)}</div>'
    )


# ═══════════════════════════════════════════
# 规则管理
# ═══════════════════════════════════════════

@router.get("/rules")
async def rules_page(request: Request):
    """规则管理页"""
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/rules_list.html",
    })


@router.get("/rules/list")
async def rules_list(request: Request, dimension: str = ""):
    """规则列表 HTML 片段（支持按维度筛选）"""
    with get_db() as conn:
        if dimension:
            rows = conn.execute(
                """SELECT * FROM classification_rules WHERE dimension = ?
                   ORDER BY dimension, priority DESC, hit_count DESC""",
                (dimension,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM classification_rules
                   ORDER BY dimension, priority DESC, hit_count DESC"""
            ).fetchall()

    # 统计各维度规则数
    with get_db() as conn:
        stats = conn.execute(
            "SELECT dimension, COUNT(*) as cnt, SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) as active "
            "FROM classification_rules GROUP BY dimension ORDER BY dimension"
        ).fetchall()

    from app.main import templates
    return templates.TemplateResponse(request, "partials/rules_table.html", {
        "rules": [dict(r) for r in rows],
        "stats": [dict(s) for s in stats],
        "filter_dimension": dimension,
    })


@router.get("/rules/sub-fields")
async def sub_fields(request: Request, dimension: str = ""):
    """返回某维度下已有的子字段列表（用于自动补全）"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT sub_field FROM classification_rules
               WHERE dimension = ? AND sub_field IS NOT NULL AND sub_field != ''
               ORDER BY sub_field""",
            (dimension,),
        ).fetchall()
    return [r["sub_field"] for r in rows]


@router.post("/rules/create")
async def create_rule(
    request: Request,
    dimension: str = Form(...),
    sub_field: str = Form(""),
    pattern: str = Form(...),
    match_type: str = Form("keyword"),
    priority: int = Form(0),
    threshold: float = Form(0.6),
):
    """创建新规则"""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules
               (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (dimension, sub_field, pattern, match_type, priority, threshold),
        )

    # 返回更新后的列表
    from app.main import templates
    return HTMLResponse(
        f"""<div hx-get="/rules/list?dimension={dimension}" hx-trigger="load" hx-swap="outerHTML"></div>
        <p style="color:green;margin-top:0.5rem">✅ 规则已添加：{dim_labels.get(dimension, dimension)} → {pattern}</p>"""
    )


@router.post("/rules/{rule_id}/toggle")
async def toggle_rule(request: Request, rule_id: int):
    """启用/禁用规则 — 返回更新行 + 内联 stats OOB swap"""
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_rules SET is_active = 1 - is_active, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (rule_id,),
        )
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()

    from app.main import templates

    row_html = templates.get_template("partials/rules_row.html").render({
        "rule": dict(rule),
    })

    # 行 HTML + 统计面板 OOB swap（一次响应同时更新两处）
    return HTMLResponse(row_html + "\n" + _render_stats_oob())


@router.post("/rules/{rule_id}/lock")
async def toggle_rule_lock(request: Request, rule_id: int):
    """锁定/解锁规则（锁定后不纳入僵尸规则判断，预置/长期保留规则用）— 返回更新行"""
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_rules SET locked = 1 - COALESCE(locked, 0), "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (rule_id,),
        )
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        if not rule:
            return HTMLResponse("", status_code=404)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/rules_row.html", {
        "rule": dict(rule),
    })


@router.get("/rules/stats")
async def rules_stats(request: Request):
    """规则统计面板 HTML 片段（供 HTMX 局部刷新）"""
    oob_html = _render_stats_oob()
    stats_html = oob_html.replace(
        'hx-swap-oob="true"',
        'hx-get="/rules/stats" hx-trigger="statsRefresh from:body" hx-swap="outerHTML"'
    )
    return HTMLResponse(stats_html)


@router.delete("/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: int):
    """删除规则 — 返回完整规则列表 + 统计面板"""
    with get_db() as conn:
        conn.execute("DELETE FROM classification_rules WHERE id = ?", (rule_id,))
    return await rules_list(request)


@router.put("/rules/{rule_id}")
async def update_rule(
    request: Request,
    rule_id: int,
    dimension: str = Form(...),
    pattern: str = Form(...),
    sub_field: str = Form(""),
    match_type: str = Form("keyword"),
    priority: int = Form(0),
    threshold: float = Form(0.6),
):
    """编辑规则"""
    from fastapi.responses import JSONResponse

    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        if not existing:
            return JSONResponse({"detail": "规则不存在"}, status_code=404)

        conn.execute(
            """UPDATE classification_rules
               SET dimension = ?, sub_field = ?, pattern = ?, match_type = ?,
                   priority = ?, threshold = ?, updated_at = datetime('now','localtime')
               WHERE id = ?""",
            (dimension, sub_field, pattern, match_type, priority, threshold, rule_id),
        )

    # 返回更新后的规则行 HTML 片段（供 htmx 替换）
    from app.main import templates
    return templates.TemplateResponse(request, "partials/rules_row.html", {
        "rule": dict(existing) | {
            "dimension": dimension, "sub_field": sub_field, "pattern": pattern,
            "match_type": match_type, "priority": priority, "threshold": threshold,
        }
    })


# ═══════════════════════════════════════════
# 审核队列
# ═══════════════════════════════════════════

@router.get("/review")
async def review_page(request: Request):
    """审核队列页"""
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/review_list.html",
    })


@router.get("/review/list")
async def review_list(request: Request):
    """待审核项列表"""
    with get_db() as conn:
        # 获取所有待审核的队列项（状态为 review）
        rows = conn.execute(
            """SELECT q.id as queue_id, q.clause_id, q.dimension, q.keyword_score,
                      q.ai_label, q.ai_confidence, q.status,
                      c.clause_no, c.title as clause_title, c.content,
                      s.code as spec_code, s.title as spec_title
               FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               JOIN specifications s ON c.spec_id = s.id
               WHERE q.status = 'review'
               ORDER BY q.created_at DESC LIMIT 50"""
        ).fetchall()

    from app.main import templates
    return templates.TemplateResponse(request, "partials/review_list.html", {
        "items": [dict(r) for r in rows],
    })


@router.post("/review/{queue_id}/confirm")
async def confirm_review(request: Request, queue_id: int):
    """确认 AI 分类标签 — 触发反馈闭环"""
    from app.classifier.feedback import process_feedback

    with get_db() as conn:
        item = conn.execute(
            "SELECT clause_id, dimension, ai_label, ai_confidence "
            "FROM classification_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()

    if item:
        process_feedback(item["clause_id"], item["dimension"], item["ai_label"],
                         source_conf=item["ai_confidence"] or 0.0)

    # 返回更新后的列表
    from app.main import templates
    return HTMLResponse(
        """<div hx-get="/review/list" hx-trigger="load" hx-swap="outerHTML"></div>
        <p style="color:green">✅ 已确认并提取关键词</p>"""
    )


@router.post("/review/{queue_id}/reject")
async def reject_review(request: Request, queue_id: int):
    """驳回 AI 分类标签"""
    with get_db() as conn:
        item = conn.execute(
            "SELECT clause_id FROM classification_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if item:
            conn.execute(
                "UPDATE classification_queue SET status = 'rejected', ai_label = NULL WHERE id = ?",
                (queue_id,),
            )
            conn.execute(
                "UPDATE clauses SET needs_review = 0 WHERE id = ?", (item["clause_id"],)
            )

    from app.main import templates
    return HTMLResponse(
        """<div hx-get="/review/list" hx-trigger="load" hx-swap="outerHTML"></div>
        <p style="color:orange">⚠️ 已驳回</p>"""
    )


@router.post("/review/batch-confirm")
async def batch_confirm(request: Request, body: dict):
    """批量确认复核项（逐条走 process_feedback 反馈闭环）"""
    from app.classifier.feedback import process_feedback
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("queue_ids") or []
    if not isinstance(ids, list):
        return _JR({"detail": "queue_ids 须为数组"}, status_code=400)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, clause_id, dimension, ai_label, ai_confidence FROM classification_queue "
            f"WHERE status='review' AND id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
    for item in rows:
        process_feedback(item["clause_id"], item["dimension"], item["ai_label"],
                         source_conf=item["ai_confidence"] or 0.0)
    return await review_list(request)


@router.post("/review/batch-reject")
async def batch_reject(request: Request, body: dict):
    """批量驳回复核项"""
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("queue_ids") or []
    if not isinstance(ids, list):
        return _JR({"detail": "queue_ids 须为数组"}, status_code=400)
    if ids:
        with get_db() as conn:
            for i in ids:
                item = conn.execute(
                    "SELECT clause_id FROM classification_queue WHERE id = ? AND status='review'", (i,)
                ).fetchone()
                if item:
                    conn.execute(
                        "UPDATE classification_queue SET status='rejected', ai_label=NULL WHERE id=?",
                        (i,),
                    )
                    conn.execute("UPDATE clauses SET needs_review=0 WHERE id=?", (item["clause_id"],))
    return await review_list(request)


# ═══════════════════════════════════════════
# AI 分类运行
# ═══════════════════════════════════════════

@router.post("/classify/run")
async def run_classifier(request: Request):
    """手动触发 AI 批量分类（force 模式，不限满 20 条）"""
    from app.ai.classifier_ai import process_pending_batches

    try:
        count = process_pending_batches(force=True)
    except Exception as e:
        return HTMLResponse(f"""<p style="color:red">❌ 分类失败: {e}</p>""")

    return HTMLResponse(f"""<p style="color:green">✅ AI 分类完成：{count} 条已处理</p>
    <div hx-get="/rules/list" hx-trigger="load" hx-swap="outerHTML"></div>""")


# ═══════════════════════════════════════════
# 队列统计
# ═══════════════════════════════════════════

@router.get("/queue/stats")
async def queue_stats(request: Request):
    """队列统计（JSON）"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT dimension, status, COUNT(*) as cnt
               FROM classification_queue
               GROUP BY dimension, status
               ORDER BY dimension, status"""
        ).fetchall()

    stats = {}
    for r in rows:
        dim = r["dimension"]
        if dim not in stats:
            stats[dim] = {"total": 0, "pending": 0, "ai_processing": 0, "auto_adopted": 0, "review": 0}
        stats[dim][r["status"]] = r["cnt"]
        stats[dim]["total"] += r["cnt"]

    return stats


# ═══════════════════════════════════════════
# 规则质量报表（三类异常 + 一键处理 + 导出）
# ═══════════════════════════════════════════

import json as _json
from fastapi.responses import JSONResponse


def _quality_rows(conn):
    """规则质量三类（供报表渲染/一键处理/导出共用）"""
    ratio = 0.8  # 与 RULE_AUTO_ENABLE_RATIO 同值（防止 import 环可复制常量语义）
    min_hit = 5
    suggest_enable = conn.execute(
        """SELECT * FROM classification_rules
           WHERE is_active = 0 AND hit_count >= ? AND confirmed * 1.0 / hit_count >= ?
           ORDER BY hit_count DESC""",
        (min_hit, ratio),
    ).fetchall()
    suggest_disable = conn.execute(
        """SELECT * FROM classification_rules
           WHERE is_active = 1 AND confirmed > 0 AND hit_count > 10 AND confirmed * 1.0 / hit_count < 0.3
           ORDER BY hit_count DESC"""
    ).fetchall()
    zombie = conn.execute(
        """SELECT * FROM classification_rules
           WHERE hit_count = 0 AND (locked IS NULL OR locked = 0)
             AND created_at < datetime('now', 'localtime', '-30 days')
           ORDER BY created_at"""
    ).fetchall()
    return {
        "suggest_enable": [dict(r) for r in suggest_enable],
        "suggest_disable": [dict(r) for r in suggest_disable],
        "zombie": [dict(r) for r in zombie],
    }


def _action_sql(action: str, kind: str) -> tuple[str, list]:
    """返回批量动作的 UPDATE/DELETE SQL 与参数（kind 决定过滤条件）"""
    conds = {
        "suggest_enable": "is_active = 0 AND hit_count >= 5 AND confirmed * 1.0 / hit_count >= 0.8",
        "suggest_disable": "is_active = 1 AND confirmed > 0 AND hit_count > 10 AND confirmed * 1.0 / hit_count < 0.3",
        "zombie": "hit_count = 0 AND (locked IS NULL OR locked = 0) AND created_at < datetime('now','localtime','-30 days')",
    }
    cond = conds[kind]
    if action == "delete_all":
        return f"DELETE FROM classification_rules WHERE {cond}", []
    if action == "enable_all":
        return f"UPDATE classification_rules SET is_active = 1, updated_at = datetime('now','localtime') WHERE {cond}", []
    return f"UPDATE classification_rules SET is_active = 0, updated_at = datetime('now','localtime') WHERE {cond}", []


@router.get("/rules/quality")
async def rules_quality(request: Request):
    """规则质量报表 HTML 片段"""
    with get_db() as conn:
        q = _quality_rows(conn)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/rules_quality.html", {"quality": q})


@router.post("/rules/quality/batch")
async def rules_quality_batch(request: Request,
                              action: str = Form(""), kind: str = Form("")):
    """批量处理：enable_all/disable_all/delete_all × kind

    前端按钮用 htmx hx-vals（默认 urlencoded 表单），故端点收 Form 而非 JSON body。
    """
    if action not in ("enable_all", "disable_all", "delete_all") or kind not in (
        "suggest_enable", "suggest_disable", "zombie"
    ):
        return JSONResponse({"detail": "非法参数"}, status_code=400)
    with get_db() as conn:
        sql, params = _action_sql(action, kind)
        conn.execute(sql, params)
    with get_db() as conn:
        q = _quality_rows(conn)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/rules_quality.html", {"quality": q})


@router.get("/rules/quality/export")
async def rules_quality_export(request: Request):
    """导出规则质量汇总 JSON 附件"""
    import time as _t
    with get_db() as conn:
        q = _quality_rows(conn)
    ts = _t.strftime("%Y%m%d_%H%M%S")
    return JSONResponse(q, headers={
        "Content-Disposition": f'attachment; filename="rule_quality_{ts}.json"'
    })
