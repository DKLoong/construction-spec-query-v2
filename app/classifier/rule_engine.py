import re
from app.config import ADAPTIVE_THRESHOLDS


def _match_score(text: str, rule: dict) -> float:
    """单条规则对文本的匹配得分"""
    pattern = rule["pattern"]
    match_type = rule.get("match_type", "keyword")
    priority = rule.get("priority", 1)

    if match_type == "exact":
        if pattern == text.strip():
            return 1.0 * (1 + 0.1 * priority)
        return 0.0
    elif match_type == "regex":
        try:
            matches = list(re.finditer(pattern, text))
            if matches:
                coverage = sum(m.end() - m.start() for m in matches) / max(len(text), 1)
                return min(1.0, coverage * 2) * (1 + 0.1 * priority)
        except re.error:
            return 0.0
        return 0.0
    else:  # keyword
        count = text.count(pattern)
        if count == 0:
            return 0.0
        return min(1.0, 0.3 + count * 0.15) * (1 + 0.1 * priority)


def classify_clause(clause_text: str, parent_path: list[str],
                    active_rules: list[dict]) -> tuple[dict[str, float], dict[str, str], dict[str, int]]:
    """对单条条文执行规则匹配，返回 (六维得分, 最佳标签, 最佳规则ID) 元组

    每一条规则必须满足「匹配得分 ≥ 规则自身的阈值」才参与竞争。
    """
    dims = ["dim1", "dim2", "dim3", "dim4", "dim5", "dim6"]
    scores = {d: 0.0 for d in dims}
    best_labels: dict[str, str] = {}
    best_rule_ids: dict[str, int] = {}

    # 父路径关键词也加入匹配文本（标签继承）
    augmented_text = clause_text + " " + " ".join(parent_path)

    for rule in active_rules:
        if not rule.get("is_active", 1):
            continue
        dim = rule["dimension"]
        if dim not in scores:
            continue
        score = _match_score(augmented_text, rule)
        rule_threshold = rule.get("threshold", 0.6)
        # 必须超过规则自身的阈值才参与该维度的竞争
        if score >= rule_threshold and score > scores[dim]:
            scores[dim] = score
            best_labels[dim] = rule["pattern"]
            best_rule_ids[dim] = rule["id"]

    return scores, best_labels, best_rule_ids


def should_use_ai(dimension: str, scores: dict[str, float],
                  thresholds: dict[str, float] | None = None) -> bool:
    """判断该维度是否需要 AI 辅助分类"""
    if thresholds is None:
        thresholds = ADAPTIVE_THRESHOLDS
    threshold = thresholds.get(dimension, 0.6)
    return scores.get(dimension, 0.0) < threshold
