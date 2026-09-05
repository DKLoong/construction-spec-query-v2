import traceback
from collections import Counter
from app.database import get_db
from app.logging_util import log_action, json_detail


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
    闸门④：确认标签（含词典外新词）先 upsert 入典（source=review）并提交、失效缓存，
    再 bump 规则——此刻 label ∈ 词典被闸门③放行（非空词典下高置信新词不误压）。
    规则沉淀失败仅记 ERROR 日志、不重抛（人工打标已落库，失败仅影响后续自动命中）。
    """
    from app.termdict import upsert_term_label, invalidate_term_cache, DIMS

    # 事务①：先读条文内容（无则提前返回，不入典不 bump），再写列 + 确认标签入典。
    # 必须先行提交并失效缓存：闸门③ is_valid_label 走独立连接读 term_labels，只能看到
    # 已提交行；同事务内未提交的 upsert 对其不可见 → 非空词典下高置信新词会被误压为
    # is_active=0（Task5 评审 I1）。
    with get_db() as conn:
        row = conn.execute("SELECT content FROM clauses WHERE id = ?", (clause_id,)).fetchone()
        if not row:
            return
        content = row["content"]
        conn.execute(
            "UPDATE classification_queue SET status = 'done' WHERE clause_id = ? AND dimension = ?",
            (clause_id, dimension),
        )
        col = _dim_to_column(dimension)
        conn.execute(
            f"UPDATE clauses SET {col} = ?, ai_classified = 1, needs_review = 0 WHERE id = ?",
            (confirmed_label, clause_id),
        )
        if dimension in DIMS:
            upsert_term_label(conn, dimension, confirmed_label,
                              canonical=confirmed_label, source="review")
    invalidate_term_cache()

    # 事务②：提取关键词逐个沉淀规则（label 已入典提交，闸门③放行）。整体 try/except
    # 兜底：沉淀失败不重抛——人工打标已落库，规则丢失仅影响后续自动命中。log_action
    # 在事务② with 块退出（rollback）之后自开连接写日志，不介入失败事务。
    try:
        with get_db() as conn:
            keywords = extract_keywords(content, top_n=3)
            sub_field = {"dim4": "specialty", "dim5": "location", "dim6": "material"}.get(dimension, "")
            from app.classifier.rule_sink import bump_rule
            from app.params.registry import get_param_float
            enable_conf = get_param_float("classify.rule_auto_enable_conf")
            for kw in keywords:
                bump_rule(conn, dimension, kw, sub_field,
                          is_confirmed=True,
                          new_rule_active=(source_conf >= enable_conf),
                          label=confirmed_label)
    except Exception as e:
        log_action("review", "ERROR", "人工确认规则沉淀失败",
                   detail=json_detail({
                       "clause_id": clause_id, "dimension": dimension,
                       "label": confirmed_label,
                       "error": f"{type(e).__name__}: {e}",
                       "traceback": traceback.format_exc(),
                   }))


def _dim_to_column(dim: str) -> str:
    return {
        "dim4": "dim4_specialty",
        "dim5": "dim5_location",
        "dim6": "dim6_material",
    }.get(dim, dim)
