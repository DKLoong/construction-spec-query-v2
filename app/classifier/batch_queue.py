import uuid
from app.database import get_db
from app.logging_util import log_action, json_detail
from app.termdict import is_valid_label


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
    from app.params.registry import get_param_float
    conf_threshold = get_param_float("classify.ai_confidence_threshold")
    auto_count = 0
    with get_db() as conn:
        for r in results:
            q_row = conn.execute(
                "SELECT dimension FROM classification_queue WHERE clause_id = ? AND batch_id = ?",
                (r["clause_id"], batch_id)).fetchone()
            dim = q_row["dimension"] if q_row else ""
            # 闸门②：即使置信达标，label ∉ 权威词典也降级 review（人工背书扩字典）
            adopted = (r["confidence"] >= conf_threshold
                       and bool(dim)
                       and is_valid_label(dim, r["label"] or ""))
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
                # 半监督闭环：auto_adopted 也沉淀规则（confirmed 不累加）
                from app.classifier.feedback import extract_keywords
                from app.classifier.rule_sink import bump_rule
                content_row = conn.execute(
                    "SELECT content FROM clauses WHERE id = ?", (r["clause_id"],)
                ).fetchone()
                if content_row:
                    sub_field = _DIM_SUB_FIELD.get(dim, "")
                    for kw in extract_keywords(content_row["content"] or "", top_n=3):
                        # label=AI 采纳的分类标签：匹配词 kw 只负责命中，赋值写 label
                        bump_rule(conn, dim, kw, sub_field,
                                  is_confirmed=False, new_rule_active=True,
                                  label=r["label"])
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
    - classification_rules 中该维度激活规则的 pattern（按 priority/hit_count 降序）
    - clauses 表中该维度已填写的值（拆逗号分隔的多标签）
    结果去重保序，优先规则关键词（更稳定）。
    """
    if limit is None:
        from app.params.registry import get_param_int
        limit = get_param_int("classify.label_candidate_limit")
    col = _DIM_COLUMN.get(dimension)
    candidates: list[str] = []
    seen: set[str] = set()

    with get_db() as conn:
        rules = conn.execute(
            """SELECT pattern FROM classification_rules
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
