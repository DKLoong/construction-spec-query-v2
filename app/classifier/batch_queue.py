import uuid
from app.config import BATCH_SIZE
from app.database import get_db


def add_to_queue(clause_id: int, dimension: str, keyword_score: float):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, ?)",
            (clause_id, dimension, keyword_score),
        )


def get_pending_batch(dimension: str, force: bool = False) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT q.*, c.content, c.clause_no, c.title as clause_title,
                      s.code as spec_code, s.title as spec_title
               FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               JOIN specifications s ON c.spec_id = s.id
               WHERE q.dimension = ? AND q.status = 'pending'
               ORDER BY q.created_at LIMIT ?""",
            (dimension, BATCH_SIZE),
        ).fetchall()

        if not rows:
            return []

        # force=True 时不检查满批条件
        if not force and len(rows) < BATCH_SIZE:
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
    with get_db() as conn:
        for r in results:
            status = "auto_adopted" if r["confidence"] >= 0.7 else "review"
            conn.execute(
                """UPDATE classification_queue
                   SET ai_label = ?, ai_confidence = ?, status = ?
                   WHERE batch_id = ? AND clause_id = ?""",
                (r["label"], r["confidence"], status, batch_id, r["clause_id"]),
            )
            if status == "auto_adopted":
                q_row = conn.execute(
                    "SELECT dimension FROM classification_queue WHERE clause_id = ? AND batch_id = ?",
                    (r["clause_id"], batch_id),
                ).fetchone()
                if q_row:
                    dim = q_row["dimension"]
                    col = _dim_to_column(dim)
                    conn.execute(
                        f"UPDATE clauses SET {col} = ?, ai_classified = 1 WHERE id = ?",
                        (r["label"], r["clause_id"]),
                    )


_DIM_COLUMN = {
    "dim4": "dim4_specialty",
    "dim5": "dim5_location",
    "dim6": "dim6_material",
}


def collect_label_candidates(dimension: str, limit: int = 40) -> list[str]:
    """收集某维度已有标签候选，供 AI 分类 prompt 约束标签口径

    来源合并：
    - classification_rules 中该维度激活规则的 pattern（按 priority/hit_count 降序）
    - clauses 表中该维度已填写的值（拆逗号分隔的多标签）
    结果去重保序，优先规则关键词（更稳定）。
    """
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
