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


def process_feedback(clause_id: int, dimension: str, confirmed_label: str,
                     source_conf: float = 0.0):
    """人工确认反馈：写回分类后，对提取关键词逐个沉淀规则（复用 rule_sink.bump_rule）。

    source_conf 为 AI 置信度；≥ RULE_AUTO_ENABLE_CONF 时新规则初始启用。
    """
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
        from app.classifier.rule_sink import bump_rule
        from app.config import RULE_AUTO_ENABLE_CONF
        for kw in keywords:
            bump_rule(conn, dimension, kw, sub_field,
                      is_confirmed=True,
                      new_rule_active=(source_conf >= RULE_AUTO_ENABLE_CONF))


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
