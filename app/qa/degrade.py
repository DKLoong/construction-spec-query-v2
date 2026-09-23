"""精排降级级别建模与分数语义。

三级降级链（见设计文档 §4.13）：
  1. crossencoder —— CrossEncoder 可用，分数 = 模型输出（0~1），有绝对相关度语义
  2. vector       —— 降级 bi-encoder 余弦相似度（-1~1），有绝对语义，用独立阈值集
  3. none         —— 两者皆不可用，**无任何绝对相关度语义**

第 3 级的关键：候选只有 RRF 排名，第 30 名的条文未必不相关。因此
- 不设绝对丢弃线（min_score = 0，不丢任何条）
- 强弱按**排名**切分：前 1/3 为高相关（全文），后 2/3 为次相关（摘要）

历史上第 3 级把分数拍平成全 1.0，导致 tier_items 把全部候选判为 high、
摘要压缩完全失效、token 预算迅速耗尽。本模块的 rank_scores 即为修正。
"""

# 降级级别常量（禁止在业务代码中散落字符串字面量）
RERANK_CE = "crossencoder"
RERANK_VECTOR = "vector"
RERANK_NONE = "none"

# 第 3 级降级：按排名切分强弱，前 1/3 为高相关
_RANK_HIGH_RATIO = 2.0 / 3.0


def rank_scores(n: int) -> list[float]:
    """把 1-based 排名归一化为 [0, 1) 的分数，降序（首位最大）。

    仅表达**相对次序**，不表达绝对相关度。n=0 时返回空列表。
    用途：第 3 级降级时替代「全 1.0」，使 tier_items 能按排名切分强弱。
    """
    if n <= 0:
        return []
    return [(n - i) / n for i in range(1, n + 1)]


def resolve_thresholds(rerank_used: str, get_float) -> tuple[float, float]:
    """按实际生效的精排级别解析 (min_score, high_threshold)。

    get_float(key) 由调用方注入（通常为 app.qa.config.get_qa_float），
    以便本模块可独立测试。
    """
    if rerank_used == RERANK_CE:
        return get_float("rerank.min_score"), get_float("rerank.high_threshold")
    if rerank_used == RERANK_VECTOR:
        return get_float("vector.min_score"), get_float("vector.high_threshold")
    # 第 3 级：无绝对语义 → 不丢条 + 按排名切强弱
    return 0.0, _RANK_HIGH_RATIO
