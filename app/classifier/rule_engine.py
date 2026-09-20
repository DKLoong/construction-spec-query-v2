import re

from app.lexicon.store import LexiconRow

# 父标题噪声黑名单：无意义通用父标题不进入规则匹配文本
# 禁止放入真实专业词（如 钢筋/混凝土/主体结构 等），否则会误杀合法标签继承
_PARENT_NOISE_TITLES = {
    "总则", "术语", "定义", "符号", "一般规定", "附录", "引用文件",
    "规范性引用文件", "目次", "前言", "说明",
}


def _match_score(text: str, rule: dict) -> float:
    """单条规则对文本的匹配得分"""
    pattern = rule["pattern"]
    match_type = rule.get("match_type", "keyword")
    priority = rule.get("priority", 1)

    if match_type == "exact":
        # 已不再对外提供（规则页已移除该选项）：此处 text 是「归一化后的正文 + 过滤后的
        # 父路径」拼接串，要求 pattern 与之逐字相等，等于为每条条文抄一遍全文——实战
        # 不可能命中（有父路径时连抄原文都不成立）。实测库内 113/113 规则均为 keyword，
        # 该模式从未被使用。分支保留仅为兼容历史数据，避免对存量行产生静默行为变更。
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
                    active_rules: list[dict],
                    synonyms: list[LexiconRow] | None = None
                    ) -> tuple[dict[str, float], dict[str, str], dict[str, int]]:
    """对单条条文执行规则匹配，返回 (六维得分, 最佳标签, 最佳规则ID) 元组

    每一条规则必须满足「匹配得分 ≥ 规则自身的阈值」才参与竞争。
    synonyms: lexicon.LexiconRow 等价组行（kind=synonym/alias，variants→canonical）；
    为 None 时自动从词库加载（带缓存）。测试可显式注入列表以隔离数据库依赖。
    """
    dims = ["dim1", "dim2", "dim3", "dim4", "dim5", "dim6"]
    scores = {d: 0.0 for d in dims}
    best_labels: dict[str, str] = {}
    best_rule_ids: dict[str, int] = {}

    # 先归一化再匹配：词库 equiv 组（synonym/alias）把变体归并为 canonical + 父路径噪声过滤
    if synonyms is None:
        from app.lexicon.store import load_equivalent_groups
        synonyms = load_equivalent_groups()
    from app.lexicon.normalize import normalize_text
    clause_text = normalize_text(clause_text, synonyms)
    filtered_parent = [
        normalize_text(p, synonyms)
        for p in parent_path
        if p not in _PARENT_NOISE_TITLES
    ]
    # 父路径关键词也加入匹配文本（标签继承）
    augmented_text = clause_text + " " + " ".join(filtered_parent)

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
            # 规则赋值 label（沉淀规则存确认标签，避免把匹配词直接当标签）；
            # 旧规则 label 为 NULL 时兼容回退用 pattern（关键词即标签的旧语义）
            best_labels[dim] = rule.get("label") or rule["pattern"]
            best_rule_ids[dim] = rule["id"]

    return scores, best_labels, best_rule_ids


def should_use_ai(dimension: str, scores: dict[str, float],
                  thresholds: dict[str, float] | None = None) -> bool:
    """判断该维度是否需要 AI 辅助分类

    thresholds 未显式传入时，从参数注册表读 DB 覆盖后的 dim1~6 阈值（热生效）。
    """
    if thresholds is None:
        from app.params.registry import get_adaptive_thresholds
        thresholds = get_adaptive_thresholds()
    threshold = thresholds.get(dimension, 0.6)
    return scores.get(dimension, 0.0) < threshold
