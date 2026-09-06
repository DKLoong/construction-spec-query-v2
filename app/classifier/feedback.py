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
    """人工确认：写列 + 只对勾选词面 patterns 背书（废除原 top3 全 bump 的隐式夹带）。

    patterns=None/空 → 纯打标（写列、不沉淀任何词）。patterns 中的词对
    (dimension, pattern, confirmed_label) 组合做 approved 背书并 bump(confirmed=1)。
    已 rejected 的组合：人工显式批准 → 覆盖为 approved；无行则直插 approved；
    已 approved 则幂等（仅 bump 命中/确认递增）。
    """
    from app.classifier.rule_pending import KEY_DIMS, insert_pending, key_state
    from app.classifier.rule_sink import bump_rule
    from app.params.registry import get_param_float
    patterns = [p for p in (patterns or []) if p and p.strip()]
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
        if dimension in KEY_DIMS and patterns:
            enable_conf = get_param_float("classify.rule_auto_enable_conf")
            sub_field = {"dim4": "specialty", "dim5": "location",
                         "dim6": "material"}.get(dimension, "")
            for kw in patterns:
                # 人工显式背书：rejected 覆盖为 approved；无行直插 approved；已 approved 幂等
                st = key_state(conn, dimension, kw, confirmed_label)
                if st == "rejected":
                    conn.execute(
                        "UPDATE rule_pending SET status='approved', "
                        "updated_at=datetime('now','localtime') "
                        "WHERE dimension=? AND pattern=? AND label=? AND status='rejected'",
                        (dimension, kw, confirmed_label))
                elif st == "none":
                    insert_pending(conn, clause_id, dimension, kw, confirmed_label,
                                   None, "manual-confirm")
                    conn.execute(
                        "UPDATE rule_pending SET status='approved', "
                        "updated_at=datetime('now','localtime') "
                        "WHERE clause_id=? AND dimension=? AND pattern=? AND label=?",
                        (clause_id, dimension, kw, confirmed_label))
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
