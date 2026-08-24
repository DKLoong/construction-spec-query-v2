import os
import re
import time
from app.config import ADAPTIVE_THRESHOLDS

# 父标题噪声黑名单：无意义通用父标题不进入规则匹配文本
# 禁止放入真实专业词（如 钢筋/混凝土/主体结构 等），否则会误杀合法标签继承
_PARENT_NOISE_TITLES = {
    "总则", "术语", "定义", "符号", "一般规定", "附录", "引用文件",
    "规范性引用文件", "目次", "前言", "说明",
}

# 同义词模块级缓存：避免在条文循环内反复查库（TTL 内复用）
_synonym_cache: list[dict] | None = None
_synonym_cache_ts = 0.0
_synonym_cache_path: str | None = None
_SYNONYM_CACHE_TTL = 60.0  # 秒


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


def normalize_text(text: str, synonyms: list[dict] | None = None) -> str:
    """按 active 同义词把 source 替换为 target（先归一化再规则匹配）

    使用简单字符串 replace 即可（工程同义词主要方向为短词→长词，如 砼→混凝土）。
    为避免「梁」等短词抢先替换破坏「过梁」等长词，source 按长度降序处理。
    """
    if not synonyms:
        return text
    result = text
    ordered = sorted(
        synonyms, key=lambda s: len(str(s.get("source", ""))), reverse=True
    )
    for s in ordered:
        source = s.get("source", "")
        target = s.get("target", "")
        if source and target and source != target:
            result = result.replace(source, target)
    return result


def clear_synonym_cache() -> None:
    """清除同义词模块缓存（同义词增删改后调用，保证下次读取最新）"""
    global _synonym_cache, _synonym_cache_ts, _synonym_cache_path
    _synonym_cache = None
    _synonym_cache_ts = 0.0
    _synonym_cache_path = None


def _load_active_synonyms() -> list[dict]:
    """加载 active 同义词（带模块级缓存，TTL 内复用，避免循环内反复查库）"""
    global _synonym_cache, _synonym_cache_ts, _synonym_cache_path
    from app.database import get_db, DATABASE_PATH

    now = time.monotonic()
    if (
        _synonym_cache is not None
        and _synonym_cache_path == DATABASE_PATH
        and (now - _synonym_cache_ts) < _SYNONYM_CACHE_TTL
    ):
        return _synonym_cache

    try:
        if not os.path.exists(DATABASE_PATH):
            # 数据库文件不存在时不创建空文件，直接视为无同义词
            _synonym_cache = []
        else:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT source, target FROM synonym_map WHERE is_active = 1 ORDER BY id"
                ).fetchall()
            _synonym_cache = [dict(r) for r in rows]
    except Exception:
        # 表不存在 / 连接失败等异常场景一律返回空，不影响规则匹配主流程
        _synonym_cache = []

    _synonym_cache_ts = now
    _synonym_cache_path = DATABASE_PATH
    return _synonym_cache


def classify_clause(clause_text: str, parent_path: list[str],
                    active_rules: list[dict],
                    synonyms: list[dict] | None = None
                    ) -> tuple[dict[str, float], dict[str, str], dict[str, int]]:
    """对单条条文执行规则匹配，返回 (六维得分, 最佳标签, 最佳规则ID) 元组

    每一条规则必须满足「匹配得分 ≥ 规则自身的阈值」才参与竞争。
    synonyms 为 None 时自动从数据库加载 active 同义词（带模块级缓存）；
    测试可显式注入同义词列表以隔离数据库依赖。
    """
    dims = ["dim1", "dim2", "dim3", "dim4", "dim5", "dim6"]
    scores = {d: 0.0 for d in dims}
    best_labels: dict[str, str] = {}
    best_rule_ids: dict[str, int] = {}

    # 先归一化再匹配：同义词替换 + 父路径噪声标题过滤
    if synonyms is None:
        synonyms = _load_active_synonyms()
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
