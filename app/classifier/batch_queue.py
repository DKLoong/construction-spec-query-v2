import uuid
from app.config import BATCH_SIZE
from app.database import get_db


def add_to_queue(clause_id: int, dimension: str, keyword_score: float):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, ?)",
            (clause_id, dimension, keyword_score),
        )


def get_pending_batch(dimension: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT q.*, c.content FROM classification_queue q
               JOIN clauses c ON q.clause_id = c.id
               WHERE q.dimension = ? AND q.status = 'pending'
               ORDER BY q.created_at LIMIT ?""",
            (dimension, BATCH_SIZE),
        ).fetchall()

        if len(rows) < BATCH_SIZE:
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


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
