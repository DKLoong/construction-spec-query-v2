from collections import Counter
from app.database import get_db


def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """从中文文本提取语义关键词（jieba 分词，替代旧正则碎片切词）

    旧实现 `re.findall(r"[一-龥]{2,4}")` 非重叠贪心切 2-4 字块，产出「应检验屈」「服强度」
    这类无意义碎片并被当作规则（标签）→ 强行打标。改为 jieba 词典分词 + 词长/停用词过滤，
    得到「钢筋」「检验」「屈服强度」等语义词——作为规则匹配词（label 语义已由确认标签承载）。
    """
    import jieba

    stopwords = {
        "的", "和", "是", "在", "了", "不", "与", "及", "或", "应", "其", "为",
        "等", "以", "中", "对", "按", "可", "须", "均", "时", "于", "并", "则",
        "规定", "要求", "采用", "符合", "进行", "使用", "必须", "不得", "本",
    }
    words = [w.strip() for w in jieba.lcut(text or "") if w.strip()]
    filtered = [
        w for w in words
        if len(w) >= 2 and w not in stopwords
        and not w.isdigit() and not w.isspace()
    ]
    counter = Counter(filtered)
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
        from app.params.registry import get_param_float
        enable_conf = get_param_float("classify.rule_auto_enable_conf")
        for kw in keywords:
            bump_rule(conn, dimension, kw, sub_field,
                      is_confirmed=True,
                      new_rule_active=(source_conf >= enable_conf),
                      label=confirmed_label)


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
