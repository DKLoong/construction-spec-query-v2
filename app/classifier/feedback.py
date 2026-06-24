import re
from collections import Counter
from app.database import get_db


def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """从中文文本提取高频关键词（简化 TF）"""
    words = re.findall(r"[一-龥]{2,4}", text)
    stopwords = {"的", "和", "是", "在", "了", "不", "与", "及", "或", "应", "其", "为", "等", "以", "中", "对"}
    words = [w for w in words if w not in stopwords]
    counter = Counter(words)
    return [w for w, _ in counter.most_common(top_n)]


def process_feedback(clause_id: int, dimension: str, confirmed_label: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE classification_queue SET status = 'done' WHERE clause_id = ? AND dimension = ?",
            (clause_id, dimension),
        )
        col = _dim_to_column(dimension)
        conn.execute(
            f"UPDATE clauses SET {col} = ?, ai_classified = 1, needs_review = 0 WHERE id = ?",
            (confirmed_label, clause_id),
        )
        row = conn.execute("SELECT content FROM clauses WHERE id = ?", (clause_id,)).fetchone()
        if not row:
            return

        keywords = extract_keywords(row["content"], top_n=3)
        sub_field = {"dim4": "specialty", "dim5": "location", "dim6": "material"}.get(dimension, "")

        for kw in keywords:
            existing = conn.execute(
                "SELECT id, hit_count, confirmed FROM classification_rules WHERE dimension = ? AND pattern = ?",
                (dimension, kw),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                    (existing["id"],),
                )
                new_hit = existing["hit_count"] + 1
                new_confirmed = existing["confirmed"] + 1
                if new_confirmed / new_hit < 0.3 and new_hit > 10:
                    conn.execute(
                        "UPDATE classification_rules SET is_active = 0 WHERE id = ?",
                        (existing["id"],),
                    )
            else:
                conn.execute(
                    """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
                       VALUES (?, ?, ?, 'keyword', 0, 0.6, 0)""",
                    (dimension, sub_field, kw),
                )


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
