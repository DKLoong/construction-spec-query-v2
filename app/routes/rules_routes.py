"""分类规则管理 + 审核队列路由"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.models import ClassificationRuleCreate
from app.logging_util import log_action, json_detail
from app.classifier import rule_pending

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
        cur = conn.execute(
            """INSERT INTO classification_rules
               (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (dimension, sub_field, pattern, match_type, priority, threshold),
        )
        new_id = cur.lastrowid

    log_action("rule", "INFO", "新建规则",
               detail=json_detail({"rule_id": new_id, "dimension": dimension,
                                   "pattern": pattern}),
               username=getattr(request.state, "username", ""))

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

    action = "启用规则" if rule["is_active"] else "停用规则"
    log_action("rule", "INFO", action,
               detail=json_detail({"rule_id": rule_id, "is_active": rule["is_active"]}),
               username=getattr(request.state, "username", ""))

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
    action = "锁定规则" if rule["locked"] else "解锁规则"
    log_action("rule", "INFO", action,
               detail=json_detail({"rule_id": rule_id, "locked": rule["locked"]}),
               username=getattr(request.state, "username", ""))
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
    log_action("rule", "INFO", "删除规则",
               detail=json_detail({"rule_id": rule_id}),
               username=getattr(request.state, "username", ""))
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

    log_action("rule", "INFO", "编辑规则",
               detail=json_detail({"rule_id": rule_id, "dimension": dimension,
                                   "pattern": pattern}),
               username=getattr(request.state, "username", ""))

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

def _fetch_review_items(conn) -> list[dict]:
    """查询待审核队列项（status='review'），审核页与 /review/list 共用"""
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
    return [dict(r) for r in rows]


@router.get("/review")
async def review_page(request: Request):
    """审核队列页（初始渲染需带 items，否则 review_list.html 恒显示空态）"""
    from app.main import templates
    with get_db() as conn:
        items = _fetch_review_items(conn)
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/review_list.html",
        "items": items,
    })


@router.get("/review/list")
async def review_list(request: Request):
    """待审核项列表"""
    from app.main import templates
    with get_db() as conn:
        items = _fetch_review_items(conn)
    return templates.TemplateResponse(request, "partials/review_list.html", {
        "items": items,
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
        log_action("review", "INFO", "确认分类标签",
                   detail=json_detail({"queue_id": queue_id,
                                       "clause_id": item["clause_id"],
                                       "dimension": item["dimension"],
                                       "ai_label": item["ai_label"]}),
                   username=getattr(request.state, "username", ""))
        process_feedback(item["clause_id"], item["dimension"], item["ai_label"],
                         source_conf=item["ai_confidence"] or 0.0)
    else:
        log_action("review", "WARN", "确认失败-队列项不存在",
                   detail=json_detail({"queue_id": queue_id}),
                   username=getattr(request.state, "username", ""))

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
            "SELECT clause_id, dimension FROM classification_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if item:
            conn.execute(
                "UPDATE classification_queue SET status = 'rejected', ai_label = NULL WHERE id = ?",
                (queue_id,),
            )
            conn.execute(
                "UPDATE clauses SET needs_review = 0 WHERE id = ?", (item["clause_id"],)
            )

    if item:
        log_action("review", "INFO", "驳回分类标签",
                   detail=json_detail({"queue_id": queue_id,
                                       "clause_id": item["clause_id"],
                                       "dimension": item["dimension"]}),
                   username=getattr(request.state, "username", ""))

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
    log_action("review", "INFO", "批量确认",
               detail=json_detail({"count": len(rows)}),
               username=getattr(request.state, "username", ""))
    return await review_list(request)


@router.post("/review/batch-reject")
async def batch_reject(request: Request, body: dict):
    """批量驳回复核项"""
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("queue_ids") or []
    if not isinstance(ids, list):
        return _JR({"detail": "queue_ids 须为数组"}, status_code=400)
    processed = 0
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
                    processed += 1
    log_action("review", "INFO", "批量驳回",
               detail=json_detail({"count": processed}),
               username=getattr(request.state, "username", ""))
    return await review_list(request)


# ═══════════════════════════════════════════
# 词面校核（Tab2） & 黑名单管理（Tab3）
# ═══════════════════════════════════════════

# 维度 → 规则子字段（人工批准写回 confirmed 规则时填充 sub_field，与 rule_sink 语义一致）
_DIM_SUB_FIELD = {"dim4": "specialty", "dim5": "location", "dim6": "material"}


@router.get("/review/word-pending")
async def review_word_pending(request: Request, dimension: str = ""):
    """词面校核聚合（Tab2 数据源，供 Task5 UI 接）"""
    from app.main import templates
    groups = rule_pending.pending_groups(dimension or None)
    return templates.TemplateResponse(request, "partials/review_word_panel.html", {
        "groups": groups, "dimension": dimension, "dim_labels": dim_labels})


@router.post("/review/word-pending/decide")
async def review_word_decide(request: Request, body: dict):
    """词面校核反义批量裁决（键级，修订 F1/F2/F4）。

    approve → 勾选键先回填（写列+queue review→done）再置 approved，并 bump confirmed 规则；
    reject  → 勾选键置 rejected（不写列、queue 滞留）；同组未勾键转 approved 同样回填+bump
              （F2：任一动作后最终 approved 键都回填）。
    """
    from fastapi.responses import JSONResponse as _JR
    from app.classifier.rule_sink import bump_rule

    ids = body.get("ids") or []
    action = body.get("action")
    if action not in ("approve", "reject") or not isinstance(ids, list) or not ids:
        return _JR({"detail": "ids/action 不合法"}, status_code=400)

    with get_db() as conn:
        selected_keys = set(rule_pending.resolve_keys(conn, ids))
        groups = {(d, p) for d, p, _ in selected_keys}

        # 组内全部 pending 键（decide_scope 反义分配的作用域）
        pending_keys: set = set()
        for d, p in groups:
            for r in conn.execute(
                "SELECT DISTINCT label FROM rule_pending "
                "WHERE dimension=? AND pattern=? AND status='pending'",
                (d, p)).fetchall():
                pending_keys.add((d, p, r["label"]))

        # 最终转 approved 的键（approve→勾选 pending 键；reject→未勾选 pending 键）
        approved_keys = (pending_keys & selected_keys) if action == "approve" \
            else (pending_keys - selected_keys)

        # 先回填（此时键仍 pending，backfill_and_close 按 status='pending' 取来源条文）
        for d, p, l in sorted(approved_keys):
            rule_pending.backfill_and_close(conn, d, p, l)
        stats = rule_pending.decide_scope(conn, ids, action, expand=True)
        # F4：人工批准写回 confirmed 规则（幂等：已有规则仅 hit/confirmed 递增）
        for d, p, l in sorted(approved_keys):
            bump_rule(conn, d, p, _DIM_SUB_FIELD.get(d, ""),
                      is_confirmed=True, new_rule_active=True, label=l)

    log_action("review", "INFO", f"词面{'批准' if action == 'approve' else '驳回'}",
               detail=json_detail({"ids": ids, **stats}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewWordPending, reviewBlacklist"})


@router.get("/review/blacklist")
async def review_blacklist(request: Request):
    """黑名单管理（Tab3 数据源，供 Task5 UI 接）"""
    from app.main import templates
    rows = rule_pending.blacklist_rows()
    return templates.TemplateResponse(request, "partials/review_blacklist.html",
                                      {"rows": rows, "dim_labels": dim_labels})


@router.post("/review/blacklist/{pid}/restore")
async def review_blacklist_restore(request: Request, pid: int):
    """黑名单档1：该 rejected 键全部行 → pending（恢复待审）"""
    with get_db() as conn:
        ids = rule_pending._key_all_pending_ids(conn, pid, "rejected")
        n = rule_pending.set_status(conn, ids, "pending") if ids else 0
    log_action("review", "INFO", "黑名单恢复待审",
               detail=json_detail({"pid": pid, "ids": ids, "n": n}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewWordPending, reviewBlacklist"})


@router.post("/review/blacklist/{pid}/approve")
async def review_blacklist_approve(request: Request, pid: int):
    """黑名单档2：该 rejected 键来源条文回填（写列+queue done）后置 approved，并 bump confirmed"""
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        row = conn.execute(
            "SELECT dimension, pattern, label FROM rule_pending WHERE id=?", (pid,)).fetchone()
        n = 0
        if row:
            rule_pending.backfill_rejected_clauses(conn, row["dimension"], row["pattern"], row["label"])
            ids = rule_pending._key_all_pending_ids(conn, pid, "rejected")
            n = rule_pending.set_status(conn, ids, "approved") if ids else 0
            bump_rule(conn, row["dimension"], row["pattern"], _DIM_SUB_FIELD.get(row["dimension"], ""),
                      is_confirmed=True, new_rule_active=True, label=row["label"])
    log_action("review", "INFO", "黑名单批准",
               detail=json_detail({"pid": pid, "n": n}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewWordPending, reviewBlacklist"})


# ═══════════════════════════════════════════
# AI 分类运行
# ═══════════════════════════════════════════

def _pending_stats_html(stats: dict) -> str:
    """待处理统计展示片段（#3b）：行数=条文×维度项，另报涉及条文数避免口径误读"""
    rows = stats["rows"]
    clauses = stats["clauses"]
    by_dim = stats.get("by_dim", {})
    if rows == 0:
        return '<span style="color:var(--pico-muted-color);font-size:0.85rem">✔ 无待处理条文</span>'
    detail = " · ".join(
        f"{d} {v['rows']} 项" for d, v in sorted(by_dim.items())
    )
    return (
        '<span title="按条文×维度计（一条条文最多 dim4/5/6 各一项）：' + detail + '" '
        'style="color:var(--pico-muted-color);font-size:0.85rem;cursor:help">'
        f'⏳ 待处理 <b>{clauses} 条文</b> / {rows} 项</span>'
    )


@router.post("/classify/run")
async def run_classifier(request: Request):
    """手动触发 AI 批量分类（drain 模式：每维按 batch_size 分批直到待处理清空）"""
    from app.ai.classifier_ai import process_pending_batches

    try:
        count = process_pending_batches(force=True, drain=True)
    except Exception as e:
        log_action("classify", "ERROR", "运行AI分类失败",
                   detail=json_detail({"error": str(e)}),
                   username=getattr(request.state, "username", ""))
        return HTMLResponse(f"""<p style="color:red">❌ 分类失败: {e}</p>""")

    log_action("classify", "INFO", "运行AI分类",
               detail=json_detail({"count": count}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse(f"""<p style="color:green">✅ AI 分类完成：{count} 条已处理</p>
    <div hx-get="/rules/list" hx-trigger="load" hx-swap="outerHTML"></div>""")


@router.get("/classify/pending-stats")
async def classify_pending_stats(request: Request):
    """待处理统计展示片段（#3b）"""
    from app.classifier.batch_queue import get_pending_stats
    return HTMLResponse(_pending_stats_html(get_pending_stats()))


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


def _param_thresholds():
    """从参数注册表读自动启停阈值（与 rule_sink 同源，消除复制常量失同步）"""
    from app.params.registry import get_param_float, get_param_int
    return {
        "enable_ratio": get_param_float("classify.rule_auto_enable_ratio"),
        "enable_min_hit": get_param_int("classify.rule_auto_enable_min_hit"),
        "disable_ratio": get_param_float("classify.rule_disable_ratio"),
        "disable_min_hit": get_param_int("classify.rule_disable_min_hit"),
    }


def _quality_rows(conn):
    """规则质量三类（供报表渲染/一键处理/导出共用）"""
    t = _param_thresholds()
    suggest_enable = conn.execute(
        """SELECT * FROM classification_rules
           WHERE is_active = 0 AND hit_count >= ? AND confirmed * 1.0 / hit_count >= ?
           ORDER BY hit_count DESC""",
        (t["enable_min_hit"], t["enable_ratio"]),
    ).fetchall()
    suggest_disable = conn.execute(
        """SELECT * FROM classification_rules
           WHERE is_active = 1 AND confirmed > 0 AND hit_count > ?
             AND confirmed * 1.0 / hit_count < ?
           ORDER BY hit_count DESC""",
        (t["disable_min_hit"], t["disable_ratio"]),
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
    """返回批量动作的 UPDATE/DELETE SQL 与参数（kind 决定过滤条件；阈值绑定参数防失同步）"""
    t = _param_thresholds()
    conds = {
        "suggest_enable": ("is_active = 0 AND hit_count >= ? AND confirmed * 1.0 / hit_count >= ?",
                           [t["enable_min_hit"], t["enable_ratio"]]),
        "suggest_disable": ("is_active = 1 AND confirmed > 0 AND hit_count > ? "
                            "AND confirmed * 1.0 / hit_count < ?",
                            [t["disable_min_hit"], t["disable_ratio"]]),
        "zombie": ("hit_count = 0 AND (locked IS NULL OR locked = 0) "
                   "AND created_at < datetime('now','localtime','-30 days')", []),
    }
    cond, params = conds[kind]
    if action == "delete_all":
        return f"DELETE FROM classification_rules WHERE {cond}", list(params)
    if action == "enable_all":
        return (f"UPDATE classification_rules SET is_active = 1, updated_at = datetime('now','localtime') WHERE {cond}",
                list(params))
    return (f"UPDATE classification_rules SET is_active = 0, updated_at = datetime('now','localtime') WHERE {cond}",
            list(params))


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
        cur = conn.execute(sql, params)
        affected = cur.rowcount
    log_action("rule", "INFO", "规则质量批量处理",
               detail=json_detail({"action": action, "kind": kind, "affected": affected}),
               username=getattr(request.state, "username", ""))
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
