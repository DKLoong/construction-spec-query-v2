"""AI 沉淀规则「首审 + 黑名单」核心层。

裁决单元 = (clause_id, dimension, pattern, label) 四元组（rule_pending 一行）。
免审键 = (dimension, pattern, label)（优先级 rejected > approved，rejected 为最新人工决定）：
  rejected  = 同键任一 rule_pending 行 status='rejected'（黑名单，覆盖规则侧与 approved）
  approved  = 同键任一 rule_pending 行 status='approved'
             或 分类规则同 (dimension, pattern) 满足 confirmed>0 且 is_active=1
             且 (label IS NULL OR label=?)（规则侧背书；历史 NULL label 也算）
  其余      = 首次 → 待人工。

写路径（裁决/确认/驳回/恢复）必须经本模块函数，禁止散落 SQL。

语义以 Global Constraints 9-11 与 Outside Voice 评审修订 F1-F6 为准：
  - key_state：approved 需 is_active（被自动停用的历史确认规则不算背书）；
    规则侧容忍 label 为 NULL（旧规则/规则页手工建规则）。
  - decide_scope：键级裁决——勾选 id 展开为其 (dimension, pattern, label) 键，
    approve 时这些键的全部 pending 行（跨条文）置 approved、同 (dimension, pattern)
    组内其它键置 rejected；reject 反向。
  - insert_pending：同 (clause_id, dimension, pattern, label) 任一状态行已存在则
    None（不再重复）；并发由 UNIQUE 索引兜底 → 捕获 sqlite3.IntegrityError 返回 None。
"""
import sqlite3

KEY_DIMS = ("dim4", "dim5", "dim6")


def _dim_column(dim: str) -> str:
    """维度 → clauses 分类列名（写列/回填用）。"""
    return {"dim4": "dim4_specialty", "dim5": "dim5_location",
            "dim6": "dim6_material"}.get(dim, dim)


def key_state(conn, dimension: str, pattern: str, label: str) -> str:
    """组合免审状态：pending 侧 rejected > pending 侧 approved > 规则侧 approved > none。

    rejected 为最新人工决定（黑名单）：同键任一 rejected 行即 rejected（可经黑名单
    restore/approve 恢复待审/批准）；否则任一 approved 行即 approved。
    规则侧 approved（F3/F8）：同 (dimension, pattern)、confirmed>0、is_active=1、
    且 (label IS NULL OR label=?) 即背书——被自动停用的历史确认规则不算背书。
    """
    if conn.execute(
        "SELECT 1 FROM rule_pending WHERE dimension=? AND pattern=? AND label=? "
        "AND status='rejected' LIMIT 1",
        (dimension, pattern, label)).fetchone():
        return "rejected"
    if conn.execute(
        "SELECT 1 FROM rule_pending WHERE dimension=? AND pattern=? AND label=? "
        "AND status='approved' LIMIT 1",
        (dimension, pattern, label)).fetchone():
        return "approved"
    if conn.execute(
        "SELECT 1 FROM classification_rules WHERE dimension=? AND pattern=? "
        "AND confirmed > 0 AND is_active = 1 AND (label IS NULL OR label = ?) LIMIT 1",
        (dimension, pattern, label)).fetchone():
        return "approved"
    return "none"


def insert_pending(conn, clause_id: int, dimension: str, pattern: str, label: str,
                   confidence: float | None = None, batch_id: str = "") -> int | None:
    """AI 首次提案词 → pending 行。同 (clause_id, dimension, pattern, label) 已有任一
    状态行则不重插（返回 None）；并发下由 UNIQUE(dimension, pattern, label, clause_id)
    兜底，捕获 IntegrityError 返回 None。
    """
    if conn.execute(
        "SELECT 1 FROM rule_pending WHERE clause_id=? AND dimension=? AND pattern=? "
        "AND label=? LIMIT 1", (clause_id, dimension, pattern, label)).fetchone():
        return None
    try:
        cur = conn.execute(
            "INSERT INTO rule_pending (dimension, pattern, label, clause_id, ai_confidence, batch_id)"
            " VALUES (?,?,?,?,?,?)",
            (dimension, pattern, label, clause_id, confidence, batch_id))
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def pending_ids(conn, dimension: str, pattern: str, label: str) -> list[int]:
    """某键 (dimension, pattern, label) 的全部 pending 行 id。"""
    rows = conn.execute(
        "SELECT id FROM rule_pending WHERE dimension=? AND pattern=? AND label=? "
        "AND status='pending'", (dimension, pattern, label)).fetchall()
    return [r["id"] for r in rows]


def clause_pending_ids(conn, clause_id: int, dimension: str) -> list[int]:
    """某条文该维的全部 pending 行 id（Tab1 行内编辑/行内确认的作用域）。"""
    rows = conn.execute(
        "SELECT id FROM rule_pending WHERE clause_id=? AND dimension=? "
        "AND status='pending' ORDER BY id",
        (clause_id, dimension)).fetchall()
    return [r["id"] for r in rows]


def set_status(conn, ids: list[int], status: str) -> int:
    """批量置状态（参数化）。"""
    if not ids:
        return 0
    cur = conn.execute(
        f"UPDATE rule_pending SET status=?, updated_at=datetime('now','localtime') "
        f"WHERE id IN ({','.join('?' * len(ids))})",
        [status, *ids])
    return cur.rowcount


def _set_status_by_keys(conn, keys, status: str) -> int:
    """对一组键 (dimension, pattern, label) 的全部 pending 行（跨条文）置状态。"""
    n = 0
    for dimension, pattern, label in keys:
        cur = conn.execute(
            "UPDATE rule_pending SET status=?, updated_at=datetime('now','localtime') "
            "WHERE dimension=? AND pattern=? AND label=? AND status='pending'",
            (status, dimension, pattern, label))
        n += cur.rowcount
    return n


def resolve_keys(conn, ids: list[int]) -> list[tuple[str, str, str]]:
    """勾选 id 集 → 去重的 (dimension, pattern, label) 键（仅 pending 行）。

    键级裁决/回填的共享不变量：非 pending 行 id 不纳入作用域（不扩展组、不参与反义
    分配），与 decide_scope 键级语义一致。
    """
    if not ids:
        return []
    ph = ','.join('?' * len(ids))
    rows = conn.execute(
        f"SELECT DISTINCT dimension, pattern, label FROM rule_pending "
        f"WHERE id IN ({ph}) AND status='pending'",
        ids).fetchall()
    return [(r["dimension"], r["pattern"], r["label"]) for r in rows]


def decide_scope(conn, ids: list[int], action: str = "approve", expand: bool = True) -> dict:
    """反义批量（键级，修订 F1/F2）：勾选 id 集展开为其 (dimension, pattern, label) 键。

    approve → 勾选键的全部 pending 行（跨条文）置 approved，同 (dimension, pattern)
              组内其它键置 rejected；
    reject  → 反向（勾选键 rejected，其余键 approved）。
    返回 {"approved": n, "rejected": m}（n/m 为受影响的行数）。

    expand：键级裁决下作用域始终由勾选 id 的 (dimension, pattern) 组派生（含未勾键），
    expand=False 仅作为计划既有调用点兼容参数保留（语义等同 True）。
    """
    if not ids:
        return {"approved": 0, "rejected": 0}
    selected_keys = set(resolve_keys(conn, ids))
    groups = {(d, p) for d, p, _ in selected_keys}

    approved_keys: set = set()
    rejected_keys: set = set()
    for dimension, pattern in groups:
        group_rows = conn.execute(
            "SELECT DISTINCT label FROM rule_pending "
            "WHERE dimension=? AND pattern=? AND status='pending'",
            (dimension, pattern)).fetchall()
        for gr in group_rows:
            key = (dimension, pattern, gr["label"])
            checked = key in selected_keys
            if action == "approve":
                (approved_keys if checked else rejected_keys).add(key)
            else:
                (rejected_keys if checked else approved_keys).add(key)

    a = _set_status_by_keys(conn, approved_keys, "approved")
    r = _set_status_by_keys(conn, rejected_keys, "rejected")
    return {"approved": a, "rejected": r}


def pending_groups(dimension: str | None = None) -> list[dict]:
    """Tab2 词面聚合：status='pending' 按 (dimension, pattern) 聚合。

    返回 [{dimension, pattern, clause_count, avg_conf, labels:[{id,label,ai_confidence,n}]}]。
    """
    from app.database import get_db
    sql = ("SELECT dimension, pattern, COUNT(DISTINCT clause_id) AS clause_count, "
           "ROUND(AVG(ai_confidence), 3) AS avg_conf "
           "FROM rule_pending WHERE status='pending'")
    args = []
    if dimension:
        sql += " AND dimension=?"
        args.append(dimension)
    sql += " GROUP BY dimension, pattern ORDER BY dimension, clause_count DESC, pattern"
    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        groups = []
        for r in rows:
            labs = conn.execute(
                "SELECT id, label, ai_confidence, COUNT(*) AS n FROM rule_pending "
                "WHERE dimension=? AND pattern=? AND status='pending' "
                "GROUP BY label ORDER BY n DESC",
                (r["dimension"], r["pattern"])).fetchall()
            groups.append({
                "dimension": r["dimension"], "pattern": r["pattern"],
                "clause_count": r["clause_count"], "avg_conf": r["avg_conf"],
                "labels": [dict(x) for x in labs],
            })
    return groups


def pending_clause_groups(dimension: str | None = None) -> list[dict]:
    """Tab1 条文多标签视图：status='pending' 按 (dimension, clause_id) 聚合，
    join clauses/specifications 带条文文本。

    返回 [{dimension, clause_id, spec_code, clause_no, content,
           candidates:[{id, pattern, label, ai_confidence}]}]。
    """
    from app.database import get_db
    sql = ("SELECT rp.dimension, rp.clause_id, s.code AS spec_code, c.clause_no, c.content "
           "FROM rule_pending rp "
           "JOIN clauses c ON c.id = rp.clause_id "
           "JOIN specifications s ON s.id = c.spec_id "
           "WHERE rp.status='pending'")
    args = []
    if dimension:
        sql += " AND rp.dimension=?"
        args.append(dimension)
    sql += " GROUP BY rp.dimension, rp.clause_id ORDER BY rp.dimension, c.clause_no"
    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        groups = []
        for r in rows:
            cands = conn.execute(
                "SELECT id, pattern, label, ai_confidence FROM rule_pending "
                "WHERE clause_id=? AND dimension=? AND status='pending' ORDER BY id",
                (r["clause_id"], r["dimension"])).fetchall()
            groups.append({
                "dimension": r["dimension"], "clause_id": r["clause_id"],
                "spec_code": r["spec_code"], "clause_no": r["clause_no"],
                "content": r["content"],
                "candidates": [dict(x) for x in cands],
            })
    return groups


def rejected_clause_groups(dimension: str | None = None) -> list[dict]:
    """Tab1 全标签已驳条文视图：候选全 reject（无 pending）且 queue 仍 review 的条文。

    D1：驳回后条文仍在 Tab1 主表带「词已驳回」标记 + 行内编辑可用（不并入低置信兜底）。
    返回结构与 pending_clause_groups 一致（candidates 恒空），rejected_labels 携带被驳标签。
    """
    from app.database import get_db
    sql = (
        "SELECT rp.dimension, rp.clause_id, s.code AS spec_code, c.clause_no, c.content "
        "FROM rule_pending rp "
        "JOIN clauses c ON c.id = rp.clause_id "
        "JOIN specifications s ON s.id = c.spec_id "
        "WHERE rp.status = 'rejected' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM rule_pending p2 WHERE p2.clause_id = rp.clause_id "
        "    AND p2.dimension = rp.dimension AND p2.status = 'pending') "
        "AND EXISTS ("
        "  SELECT 1 FROM classification_queue q WHERE q.clause_id = rp.clause_id "
        "    AND q.dimension = rp.dimension AND q.status = 'review')"
    )
    args = []
    if dimension:
        sql += " AND rp.dimension = ?"
        args.append(dimension)
    sql += " GROUP BY rp.dimension, rp.clause_id ORDER BY rp.dimension, c.clause_no"
    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        groups = []
        for r in rows:
            rej = conn.execute(
                "SELECT label FROM rule_pending WHERE clause_id=? AND dimension=? "
                "AND status='rejected' ORDER BY id",
                (r["clause_id"], r["dimension"])).fetchall()
            groups.append({
                "dimension": r["dimension"], "clause_id": r["clause_id"],
                "spec_code": r["spec_code"], "clause_no": r["clause_no"],
                "content": r["content"],
                "candidates": [],
                "rejected_labels": [x["label"] for x in rej],
            })
    return groups


def blacklist_rows() -> list[dict]:
    """Tab3 黑名单：status='rejected' 按键聚合（词面/标签/条文数/最近驳回）。"""
    from app.database import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT dimension, pattern, label, COUNT(*) AS n, "
            "COUNT(DISTINCT clause_id) AS clause_count, MAX(updated_at) AS last_rejected "
            "FROM rule_pending WHERE status='rejected' "
            "GROUP BY dimension, pattern, label ORDER BY last_rejected DESC").fetchall()
    return [dict(r) for r in rows]


def _key_all_pending_ids(conn, pid: int, target_status: str) -> list[int]:
    """按某行 id 反查其键 (dimension, pattern, label)，返回该键全部 target_status 行 id
    （黑名单 restore/approve 用）。"""
    row = conn.execute(
        "SELECT dimension, pattern, label FROM rule_pending WHERE id=?", (pid,)).fetchone()
    if not row:
        return []
    rows = conn.execute(
        "SELECT id FROM rule_pending WHERE dimension=? AND pattern=? AND label=? AND status=?",
        (row["dimension"], row["pattern"], row["label"], target_status)).fetchall()
    return [r["id"] for r in rows]


def _backfill_by_status(conn, dimension: str, pattern: str, label: str,
                        status: str) -> int:
    """该键在指定 status 下的全部来源条文写列 + 其 queue(review) 置 done。返回写列条文数。"""
    col = _dim_column(dimension)
    rows = conn.execute(
        "SELECT DISTINCT clause_id FROM rule_pending "
        "WHERE dimension=? AND pattern=? AND label=? AND status=?",
        (dimension, pattern, label, status)).fetchall()
    cids = [r["clause_id"] for r in rows]
    if cids:
        ph = ','.join('?' * len(cids))
        conn.execute(
            f"UPDATE clauses SET {col}=?, ai_classified=1 WHERE id IN ({ph})",
            [label, *cids])
        conn.execute(
            f"UPDATE classification_queue SET status='done' WHERE clause_id IN ({ph}) "
            f"AND dimension=? AND status='review'",
            [*cids, dimension])
    return len(cids)


def backfill_and_close(conn, dimension: str, pattern: str, label: str) -> int:
    """组合被人工批准：其全部 pending 来源条文写列 + 联动清 Tab1 review 项。"""
    return _backfill_by_status(conn, dimension, pattern, label, "pending")


def backfill_rejected_clauses(conn, dimension: str, pattern: str, label: str) -> int:
    """黑名单批准（档2）：该键 rejected 来源条文写列 + queue(review)→done。"""
    return _backfill_by_status(conn, dimension, pattern, label, "rejected")
