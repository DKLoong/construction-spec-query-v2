from collections import Counter
from app.ai.text_clean import plain_text
from app.database import get_db


def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """从中文文本提取语义关键词（jieba 分词，替代旧正则碎片切词）

    旧实现 `re.findall(r"[一-龥]{2,4}")` 非重叠贪心切 2-4 字块，产出「应检验屈」「服强度」
    这类无意义碎片并被当作规则（标签）→ 强行打标。改为 jieba 词典分词 + 词长/停用词过滤，
    得到「钢筋」「检验」「屈服强度」等语义词——作为规则匹配词（label 语义已由确认标签承载）。

    **入参先过 `plain_text`**：本函数是纯词频 Counter，而 content 里的 OCR 标记
    出现频率远高于正文词（实测 `td` 1448 次），不清洗则标记必然挤进 top_n 并成为
    规则 pattern（碎片污染的历史成因之一）。
    """
    import jieba

    stopwords = {
        "的", "和", "是", "在", "了", "不", "与", "及", "或", "应", "其", "为",
        "等", "以", "中", "对", "按", "可", "须", "均", "时", "于", "并", "则",
        "规定", "要求", "采用", "符合", "进行", "使用", "必须", "不得", "本",
    }
    words = [w.strip() for w in jieba.lcut(plain_text(text)) if w.strip()]
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

    写列与 review 守卫复用 `rule_pending._confirm_clause`（与 Tab1 decide/inline 同一出口，
    含维度白名单）：仅当 queue 仍 review 时写列（已 done/改标 no-op），闭环 C8「不被覆写」。
    返回值（是否实际写列）在此无需区分，忽略即可。
    """
    from app.classifier import rule_pending

    with get_db() as conn:
        rule_pending._confirm_clause(conn, clause_id, dimension, confirmed_label)
