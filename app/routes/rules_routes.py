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
    """查询待审核队列兜底项（status='review' 且无 pending 候选词、无 rejected 标签），
    Tab1 兜底块与 /review/list 共用。有 pending 词的条文在 Tab1 主表（pending_clause_groups）、
    候选全 reject 的条文在主表「词已驳回」行（rejected_clause_groups）展示，均不在此兜底块
    重复出现；每条附 extract_keywords 候选词供确认勾选背书。"""
    from app.classifier.feedback import extract_keywords
    rows = conn.execute(
        """SELECT q.id as queue_id, q.clause_id, q.dimension, q.keyword_score,
                  q.ai_label, q.ai_confidence, q.status,
                  c.clause_no, c.title as clause_title, c.content,
                  s.code as spec_code, s.title as spec_title
           FROM classification_queue q
           JOIN clauses c ON q.clause_id = c.id
           JOIN specifications s ON c.spec_id = s.id
           WHERE q.status = 'review'
             AND NOT EXISTS (
                 SELECT 1 FROM rule_pending rp
                 WHERE rp.clause_id = q.clause_id AND rp.dimension = q.dimension
                   AND rp.status IN ('pending', 'rejected')
             )
           ORDER BY q.created_at DESC LIMIT 50"""
    ).fetchall()
    items = [dict(r) for r in rows]
    for it in items:
        it["keywords"] = extract_keywords(it["content"] or "", top_n=3)
    return items


@router.get("/review")
async def review_page(request: Request):
    """审核页（三 Tab：条文待审 / 词面校核 / 黑名单；各 Tab 由 hx-get 懒加载）"""
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/review_tabs.html",
    })


@router.get("/review/pending-count")
async def review_pending_count():
    """宫格「审核」红点数据源：待审条文组 + 词面组计数（Tab1+Tab2）。"""
    from fastapi.responses import JSONResponse
    return JSONResponse(rule_pending.pending_counts())


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
async def confirm_review(request: Request, queue_id: int, body: dict | None = None):
    """确认 AI 分类标签 — 触发反馈闭环（body.patterns=勾选词；None/空→纯打标不沉淀）"""
    from app.classifier.feedback import process_feedback

    patterns = (body or {}).get("patterns") if body else None

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
                         source_conf=item["ai_confidence"] or 0.0,
                         patterns=patterns)
    else:
        log_action("review", "WARN", "确认失败-队列项不存在",
                   detail=json_detail({"queue_id": queue_id}),
                   username=getattr(request.state, "username", ""))

    return HTMLResponse("", headers={"HX-Trigger": "reviewClausePending"})


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

    return HTMLResponse("", headers={"HX-Trigger": "reviewClausePending"})


@router.post("/review/batch-confirm")
async def batch_confirm(request: Request, body: dict):
    """批量确认复核项（逐条走 process_feedback 反馈闭环；patterns=勾选词，None→纯打标）"""
    from app.classifier.feedback import process_feedback
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("queue_ids") or []
    patterns = body.get("patterns")
    if not isinstance(ids, list):
        return _JR({"detail": "queue_ids 须为数组"}, status_code=400)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, clause_id, dimension, ai_label, ai_confidence FROM classification_queue "
            f"WHERE status='review' AND id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
    for item in rows:
        kwargs = {"source_conf": item["ai_confidence"] or 0.0}
        if patterns is not None:
            kwargs["patterns"] = patterns
        process_feedback(item["clause_id"], item["dimension"], item["ai_label"], **kwargs)
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
            rule_pending.approve_rule(conn, d, p, l)
        # 被驳回键（含 approve 反义未勾选键）联动停用未确认碎片规则（防驳了仍命中）
        for d, p, l in sorted(pending_keys - approved_keys):
            rule_pending.deactivate_fragment(conn, d, p, l)

    log_action("review", "INFO", f"词面{'批准' if action == 'approve' else '驳回'}",
               detail=json_detail({"ids": ids, **stats}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewWordPending, reviewBlacklist"})


@router.get("/review/blacklist")
async def review_blacklist(request: Request):
    """黑名单管理（Tab3 数据源，供 Task5 UI 接）；每行附代表 pid 供 restore/approve 端点"""
    from app.main import templates
    rows = rule_pending.blacklist_rows()
    with get_db() as conn:
        # 单条聚合取各 rejected 键代表 id（MIN(id)），避免逐行查询（N+1）
        reps = conn.execute(
            "SELECT dimension, pattern, label, MIN(id) AS rep_id FROM rule_pending "
            "WHERE status='rejected' GROUP BY dimension, pattern, label").fetchall()
        rep_map = {(r["dimension"], r["pattern"], r["label"]): r["rep_id"] for r in reps}
        for r in rows:
            r["id"] = rep_map.get((r["dimension"], r["pattern"], r["label"]))
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
    with get_db() as conn:
        row = conn.execute(
            "SELECT dimension, pattern, label FROM rule_pending WHERE id=?", (pid,)).fetchone()
        n = 0
        if row:
            rule_pending.backfill_rejected_clauses(conn, row["dimension"], row["pattern"], row["label"])
            ids = rule_pending._key_all_pending_ids(conn, pid, "rejected")
            n = rule_pending.set_status(conn, ids, "approved") if ids else 0
            rule_pending.approve_rule(conn, row["dimension"], row["pattern"], row["label"])
    log_action("review", "INFO", "黑名单批准",
               detail=json_detail({"pid": pid, "n": n}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewWordPending, reviewBlacklist"})


# ═══════════════════════════════════════════
# Tab1 条文待审（条文多标签 + 行内编辑）
# ═══════════════════════════════════════════

# 维度 → clauses 分类列（inline 新标签纯打标写列用）
_DIM_COLUMN = {"dim4": "dim4_specialty", "dim5": "dim5_location",
               "dim6": "dim6_material"}


@router.get("/review/clause-pending")
async def review_clause_pending(request: Request, dimension: str = ""):
    """Tab1 条文待审：pending_clause_groups（条文多标签）+ 全标签已驳条文（D1）+ 低置信 queue 兜底。"""
    from app.main import templates
    groups = rule_pending.pending_clause_groups(dimension or None)
    with get_db() as conn:
        # 单条聚合取全部 rejected 标签，按 (clause_id, dimension) 分组，避免逐条文查询（N+1）
        rej_rows = conn.execute(
            "SELECT clause_id, dimension, label FROM rule_pending "
            "WHERE status='rejected'").fetchall()
        rej_map: dict[tuple[int, str], list[str]] = {}
        for r in rej_rows:
            rej_map.setdefault((r["clause_id"], r["dimension"]), []).append(r["label"])
        for g in groups:
            g["rejected_labels"] = rej_map.get((g["clause_id"], g["dimension"]), [])
        items = _fetch_review_items(conn)
    # D1：候选全 reject（无 pending）的条文并入主表（带「词已驳回」标记 + 行内编辑），不进兜底块
    groups += rule_pending.rejected_clause_groups(dimension or None)
    return templates.TemplateResponse(request, "partials/review_clause_panel.html", {
        "groups": groups, "items": items, "dimension": dimension, "dim_labels": dim_labels,
    })


@router.post("/review/clause-pending/{clause_id}/decide")
async def review_clause_decide(request: Request, clause_id: int, body: dict):
    """Tab1 主表确认标签（C1/C3/C4/C13）：只写该条 queue ai_label 列 + queue done。

    body: {dimension: str 必填, ids:[去勾词面 id], action:"approve"}。ids 可为空=纯确认。
    approve → 写 ai_label 列 + queue done（_confirm_clause）；ids 词 rejected（停用碎片）；
    其余 pending 词保持 pending。action="reject" → 400。
    """
    from fastapi.responses import JSONResponse as _JR

    ids = body.get("ids") or []
    action = body.get("action")
    dimension = body.get("dimension")
    if action != "approve" or not isinstance(ids, list):
        return _JR({"detail": "仅支持 approve（批量驳回已移除）"}, status_code=400)
    # 元素类型校验（项目规则 1.1）：短路在任何 set/比较之前，防不可哈希元素抛
    # TypeError: unhashable type → 500
    if not all(isinstance(i, int) for i in ids):
        return _JR({"detail": "ids 须为整数数组"}, status_code=400)
    # 外部输入类型+范围校验（项目规则 1.1）：非字符串先挡下，防 dict 成员测试抛
    # TypeError: unhashable type → 500
    if not isinstance(dimension, str) or dimension not in _DIM_COLUMN:
        return _JR({"detail": "dimension 不合法"}, status_code=400)

    with get_db() as conn:
        scope = rule_pending.clause_pending_ids(conn, clause_id, dimension)
        if ids and not set(ids) <= set(scope):
            return _JR({"detail": "勾选 id 不属于该条文待审作用域"}, status_code=400)
        q = conn.execute(
            "SELECT ai_label FROM classification_queue "
            "WHERE clause_id=? AND dimension=? AND status='review'",
            (clause_id, dimension)).fetchone()
        if not q:
            return _JR({"detail": "该条文该维无 review 队列项"}, status_code=400)
        label = q["ai_label"]
        if not label:
            return _JR({"detail": "该队列项无 AI 标签，请用编辑输入"}, status_code=400)
        rule_pending._confirm_clause(conn, clause_id, dimension, label)
        if ids:
            reject_keys = rule_pending.resolve_keys(conn, ids)
            rule_pending.set_status(conn, ids, "rejected")
            for d, p, l in sorted(reject_keys):
                rule_pending.deactivate_fragment(conn, d, p, l)

    log_action("review", "INFO", "条文批准(打标解耦)",
               detail=json_detail({"clause_id": clause_id, "dimension": dimension,
                                   "label": label, "rejected_word_ids": ids}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewClausePending, reviewWordPending, reviewBlacklist"})


@router.post("/review/clause-pending/{clause_id}/inline-edit")
async def review_clause_inline_edit(request: Request, clause_id: int, body: dict):
    """Tab1 行内编辑（GC10/F7）：删标签=驳（reject）、保留=approve、新标签=只写列纯打标。

    body: {label_ids:[保留 pending id], removed_label_ids:[删除 pending id],
           new_label: str|None, dimension: str|None}。删除项→该 (pattern,label) reject；
    保留项→approve（回填+bump）；new_label 非空→只写分类列（不沉淀规则）。
    """
    from fastapi.responses import JSONResponse as _JR

    label_ids = body.get("label_ids") or []
    removed_label_ids = body.get("removed_label_ids") or []
    new_label = body.get("new_label")
    dimension = body.get("dimension")
    if not isinstance(label_ids, list) or not isinstance(removed_label_ids, list):
        return _JR({"detail": "label_ids/removed_label_ids 须为数组"}, status_code=400)

    with get_db() as conn:
        all_ids = list(label_ids) + list(removed_label_ids)
        if not dimension:
            if all_ids:
                d_row = conn.execute(
                    f"SELECT dimension FROM rule_pending "
                    f"WHERE id IN ({','.join('?' * len(all_ids))}) "
                    f"ORDER BY id DESC LIMIT 1", all_ids).fetchone()
                dimension = d_row["dimension"] if d_row else None
            if not dimension:
                dr = conn.execute(
                    "SELECT dimension FROM rule_pending WHERE clause_id=? "
                    "ORDER BY id DESC LIMIT 1",
                    (clause_id,)).fetchone()
                dimension = dr["dimension"] if dr else None

        # I1：外部输入 dimension 白名单校验（拼列名前先校验，防 SQL 注入/非法列）
        if dimension and dimension not in _DIM_COLUMN:
            return _JR({"detail": "dimension 不合法"}, status_code=400)

        if dimension:
            # I2：单条查询取全部 pending id，避免循环内逐行查询（N+1）
            pending_set = set()
            if all_ids:
                pending_set = set(r["id"] for r in conn.execute(
                    f"SELECT id FROM rule_pending WHERE id IN ({','.join('?' * len(all_ids))}) "
                    f"AND status='pending'", all_ids).fetchall())
            rem = [i for i in removed_label_ids if i in pending_set]
            keep = [i for i in label_ids if i in pending_set]
            if rem:
                rem_keys = rule_pending.resolve_keys(conn, rem)
                rule_pending.set_status(conn, rem, "rejected")
                for d, p, l in sorted(rem_keys):
                    rule_pending.deactivate_fragment(conn, d, p, l)
            if keep:
                approved_keys = rule_pending.resolve_keys(conn, keep)
                for d, p, l in sorted(approved_keys):
                    rule_pending.backfill_and_close(conn, d, p, l)
                rule_pending.set_status(conn, keep, "approved")
                for d, p, l in sorted(approved_keys):
                    rule_pending.approve_rule(conn, d, p, l)
            if new_label and str(new_label).strip():
                col = _DIM_COLUMN[dimension]
                conn.execute(
                    f"UPDATE clauses SET {col}=?, ai_classified=1, needs_review=0 WHERE id=?",
                    (str(new_label).strip(), clause_id))
                conn.execute(
                    "UPDATE classification_queue SET status='done' "
                    "WHERE clause_id=? AND dimension=? AND status='review'",
                    (clause_id, dimension))

    log_action("review", "INFO", "条文行内编辑",
               detail=json_detail({"clause_id": clause_id, "dimension": dimension,
                                   "new_label": new_label, "kept": label_ids,
                                   "removed": removed_label_ids}),
               username=getattr(request.state, "username", ""))
    return HTMLResponse("", headers={"HX-Trigger": "reviewClausePending, reviewWordPending, reviewBlacklist"})


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
