import uuid
from app.database import get_db
from app.logging_util import log_action, json_detail


def try_enqueue(conn, clause_id: int, dimension: str, keyword_score: float) -> bool:
    """入列自检守卫（#4，调用方须持有写连接）

    - 条文已删除(不存在) / 其规范为「废止」/ 非条文(clause_is_non=1) → 不入 AI；
    - 同 (clause_id, dimension) 已存在任意队列记录(含终态 auto_adopted/review/rejected)
      → 不入，防队列重复膨胀。spec 批量重跑路径先 DELETE 旧队列项再重入，不受影响。
    """
    clause = conn.execute(
        """SELECT c.clause_is_non, s.status
           FROM clauses c JOIN specifications s ON c.spec_id = s.id
           WHERE c.id = ?""",
        (clause_id,),
    ).fetchone()
    if not clause:
        return False
    if clause["clause_is_non"]:
        return False
    if clause["status"] == "废止":
        return False
    dup = conn.execute(
        """SELECT 1 FROM classification_queue
           WHERE clause_id = ? AND dimension = ? LIMIT 1""",
        (clause_id, dimension),
    ).fetchone()
    if dup:
        return False
    conn.execute(
        "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, ?)",
        (clause_id, dimension, keyword_score),
    )
    return True


def add_to_queue(clause_id: int, dimension: str, keyword_score: float) -> bool:
    """自建连接调用 try_enqueue，返回是否真正入列"""
    with get_db() as conn:
        return try_enqueue(conn, clause_id, dimension, keyword_score)


def get_pending_batch(dimension: str, force: bool = False) -> list[dict]:
    from app.params.registry import get_param_int
    batch_size = get_param_int("classify.batch_size")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT q.*, c.content, c.clause_no, c.title as clause_title,
                      s.code as spec_code, s.title as spec_title
               FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               JOIN specifications s ON c.spec_id = s.id
               WHERE q.dimension = ? AND q.status = 'pending'
               ORDER BY q.created_at LIMIT ?""",
            (dimension, batch_size),
        ).fetchall()

        if not rows:
            return []

        # force=True 时不检查满批条件
        if not force and len(rows) < batch_size:
            return []

        batch_id = uuid.uuid4().hex[:12]
        ids = [r["id"] for r in rows]
        placeholders = ','.join('?' * len(ids))
        conn.execute(
            f"UPDATE classification_queue SET batch_id = ?, status = 'ai_processing' WHERE id IN ({placeholders})",
            [batch_id] + ids,
        )
        rows_with_batch = [dict(r) for r in rows]
        for d in rows_with_batch:
            d["batch_id"] = batch_id
        return rows_with_batch


def apply_ai_results(batch_id: str, results: list[dict]):
    """AI 结果裁决流（Global Constraint 2）：逐词三态裁决，单遍提词 + 判定/沉淀共用。

    - 至少 1 词 approved → auto_adopted：写列 + 仅对 approved 词 bump（命中递增，不新建）；
    - rejected 词跳过（不沉淀/不插）；首次词 → insert_pending 入池待人工；
    - 无任一 approved 词 → 该条 status='review'（不写列、不入 auto 计数）；
    - 纯打标特例：提词为空（无词即无夹带）且 conf 达标 → 直接 auto 写列、不沉淀词。
    """
    from app.params.registry import get_param_float
    from app.classifier.rule_pending import key_state, insert_pending
    from app.classifier.feedback import extract_keywords
    from app.classifier.rule_sink import bump_rule
    conf_threshold = get_param_float("classify.ai_confidence_threshold")
    auto_count = 0
    with get_db() as conn:
        for r in results:
            q_row = conn.execute(
                "SELECT dimension FROM classification_queue WHERE clause_id = ? AND batch_id = ?",
                (r["clause_id"], batch_id)).fetchone()
            dim = q_row["dimension"] if q_row else ""
            label = r["label"] or ""
            adopted = False
            # 单遍收集：(kw, state)，判定段与沉淀段共用，禁止二次 extract / 重复 key_state
            decisions: list[tuple[str, str]] = []
            if r["confidence"] >= conf_threshold and dim:
                content_row = conn.execute(
                    "SELECT content FROM clauses WHERE id = ?", (r["clause_id"],)).fetchone()
                content = (content_row["content"] or "") if content_row else ""
                kws = extract_keywords(content, top_n=3)
                for kw in kws:
                    st = key_state(conn, dim, kw, label)
                    decisions.append((kw, st))
                    if st == "rejected":
                        continue                       # 黑名单：不沉淀不插
                    if st == "approved":
                        continue                       # 已背书：仅命中递增，不新建
                    insert_pending(conn, r["clause_id"], dim, kw,
                                   label, r["confidence"], batch_id)
                approved_kws = [kw for kw, st in decisions if st == "approved"]
                # 写列条件：≥1 已背书词，或条文无提词（无词即无夹带，允许高置信直接标）
                adopted = bool(approved_kws) or not kws
            status = "auto_adopted" if adopted else "review"
            if status == "auto_adopted":
                auto_count += 1
            conn.execute(
                """UPDATE classification_queue
                   SET ai_label = ?, ai_confidence = ?, status = ?
                   WHERE batch_id = ? AND clause_id = ?""",
                (r["label"], r["confidence"], status, batch_id, r["clause_id"]),
            )
            if status == "auto_adopted":
                col = _dim_to_column(dim)
                conn.execute(
                    f"UPDATE clauses SET {col} = ?, ai_classified = 1 WHERE id = ?",
                    (r["label"], r["clause_id"]),
                )
                # 沉淀只针对已背书词（命中递增；不新建未背书规则）
                for kw, st in decisions:
                    if st == "approved":
                        bump_rule(conn, dim, kw, _DIM_SUB_FIELD.get(dim, ""),
                                  is_confirmed=False, new_rule_active=True,
                                  label=r["label"])

    # 事务已提交，写一条本批汇总（log_action 自开连接，须在 commit 后调用）
    log_action("classify", "INFO", "AI分类结果入库",
               detail=json_detail({"batch_id": batch_id, "total": len(results),
                                   "auto_adopted": auto_count,
                                   "review": len(results) - auto_count}))


_DIM_COLUMN = {
    "dim4": "dim4_specialty",
    "dim5": "dim5_location",
    "dim6": "dim6_material",
}

# 维度 → 规则子字段映射（auto_adopted 沉淀规则时填充 sub_field）
_DIM_SUB_FIELD = {
    "dim4": "specialty",
    "dim5": "location",
    "dim6": "material",
}


def collect_label_candidates(dimension: str, limit: int | None = None) -> list[str]:
    """收集某维度已有标签候选，供 AI 分类 prompt 约束标签口径

    来源合并：
    - classification_rules 中该维度激活规则的**标签**（按 priority/hit_count 降序）：
      label 有值取 label（新语义：pattern 只是匹配用的特征词），label 为空回退
      pattern（旧语义：词即标签）。取 pattern 会把「套筒/保护层/丝头」这类特征词
      混进候选标签，误导 AI 的标签口径。
    - clauses 表中该维度已填写的值（拆逗号分隔的多标签）
    结果去重保序，优先规则标签（更稳定）。
    """
    if limit is None:
        from app.params.registry import get_param_int
        limit = get_param_int("classify.label_candidate_limit")
    col = _DIM_COLUMN.get(dimension)
    candidates: list[str] = []
    seen: set[str] = set()

    with get_db() as conn:
        rules = conn.execute(
            """SELECT COALESCE(NULLIF(label, ''), pattern) AS cand
               FROM classification_rules
               WHERE dimension = ? AND is_active = 1
               ORDER BY priority DESC, hit_count DESC""",
            (dimension,),
        ).fetchall()
        vals = []
        if col:
            vals = conn.execute(
                f"SELECT DISTINCT {col} FROM clauses "
                f"WHERE {col} IS NOT NULL AND {col} != ''"
            ).fetchall()

    for rows in (rules, vals):
        for r in rows:
            for part in str(r[0]).split(","):
                v = part.strip()
                if v and v not in seen:
                    seen.add(v)
                    candidates.append(v)
    return candidates[:limit]


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)


def get_pending_stats() -> dict:
    """待处理队列统计（#3b 口径：pending 行数 / 涉及条文数 / 按维度细分）

    queue 是「条文 × 维度」粒度（一条条文最多 dim4/5/6 各一行），故 rows ≥ clauses；
    展示时两者并报避免误读「pending 行数 > 条文数=有重复」。
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM classification_queue WHERE status = 'pending'"
        ).fetchone()["n"] or 0
        clauses = conn.execute(
            "SELECT COUNT(DISTINCT clause_id) AS n FROM classification_queue "
            "WHERE status = 'pending'"
        ).fetchone()["n"] or 0
        by_rows = conn.execute(
            "SELECT dimension AS d, COUNT(*) AS n FROM classification_queue "
            "WHERE status = 'pending' GROUP BY dimension"
        ).fetchall()
        by_clauses = conn.execute(
            "SELECT dimension AS d, COUNT(DISTINCT clause_id) AS n FROM classification_queue "
            "WHERE status = 'pending' GROUP BY dimension"
        ).fetchall()
    by_dim: dict[str, dict] = {}
    for r in by_rows:
        by_dim.setdefault(r["d"], {})["rows"] = r["n"]
    for r in by_clauses:
        by_dim.setdefault(r["d"], {})["clauses"] = r["n"]
    return {"rows": rows, "clauses": clauses, "by_dim": by_dim}
