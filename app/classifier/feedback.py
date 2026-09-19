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
                     source_conf: float = 0.0, patterns: list[str] | None = None):
    """人工确认：纯打标写列 + queue done（C1 延伸 / C12 / C15）。

    patterns/source_conf 保留仅为签名兼容，忽略（不再背书词面/不再 bump 规则——
    词面普通沉淀仅 Tab2，见 spec C12）。
    仅当 queue 仍 review 时写列（已 done/改标 no-op），闭环 C8「不被覆写」。
    """
    from app.database import get_db
    col = _dim_to_column(dimension)
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE clauses SET {col}=?, ai_classified=1, needs_review=0 WHERE id=? "
            f"AND EXISTS (SELECT 1 FROM classification_queue q "
            f"WHERE q.clause_id=? AND q.dimension=? AND q.status='review')",
            (confirmed_label, clause_id, clause_id, dimension))
        if cur.rowcount:
            conn.execute(
                "UPDATE classification_queue SET status='done' "
                "WHERE clause_id=? AND dimension=? AND status='review'",
                (clause_id, dimension))


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
