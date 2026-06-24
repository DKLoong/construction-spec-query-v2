"""分类规则管理 + 审核队列路由"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.models import ClassificationRuleCreate

router = APIRouter()


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
        "right_content": "partials/qa_panel.html",
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
    dim_labels = {"dim1": "规范属性", "dim2": "工程阶段", "dim3": "工程类型",
                  "dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"}
    return HTMLResponse(
        f"""<div hx-get="/rules/list?dimension={dimension}" hx-trigger="load" hx-swap="outerHTML"></div>
        <p style="color:green;margin-top:0.5rem">✅ 规则已添加：{dim_labels.get(dimension, dimension)} → {pattern}</p>"""
    )


@router.post("/rules/{rule_id}/toggle")
async def toggle_rule(request: Request, rule_id: int):
    """启用/禁用规则"""
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_rules SET is_active = 1 - is_active, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (rule_id,),
        )
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()

    status_text = "已启用" if rule["is_active"] else "已禁用"
    active_class = "active" if rule["is_active"] else "inactive"
    from app.main import templates
    return HTMLResponse(
        f"""<span class="rule-status {active_class}" hx-swap-oob="true">{status_text}</span>
        <button hx-post="/rules/{rule_id}/toggle" hx-swap="outerHTML"
                class="{'secondary' if rule['is_active'] else ''} outline"
                style="font-size:0.8rem;padding:0.2rem 0.5rem">
            {'禁用' if rule['is_active'] else '启用'}
        </button>"""
    )


@router.delete("/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: int):
    """删除规则"""
    with get_db() as conn:
        conn.execute("DELETE FROM classification_rules WHERE id = ?", (rule_id,))
    return HTMLResponse("")


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
        "right_content": "partials/qa_panel.html",
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
            "SELECT clause_id, dimension, ai_label FROM classification_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()

    if item:
        process_feedback(item["clause_id"], item["dimension"], item["ai_label"])

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
