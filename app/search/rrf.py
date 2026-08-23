"""RRF（Reciprocal Rank Fusion）融合纯函数

将「SQL 关键词搜索」与「向量语义搜索」两路各自排好序的结果，按倒数排名
分数融合去重。本模块不关心底层是 LIKE 还是 FTS5、也不关心向量具体模型，
只接收「已排序的 clause dict 列表」，为后续无痛替换 SQL 层为 FTS5 预留解耦。
"""


def rrf_fusion(sql_results: list[dict], vector_results: list[dict],
               k: int = 60) -> list[dict]:
    """融合两路有序结果，返回按 RRF 分数降序的合并 dict 列表。

    - sql_results：已按字段优先级有序（clause_no 精确 > title > content）
    - vector_results：已按 `_distance` 升序（每个 dict 含 `_distance`）
    - 以 `dict["id"]` 为 key 去重，score = Σ 1/(k + rank)，rank 从 1 起
    - 双路都命中的条文累加两路分数，自然排前
    - 新增/覆盖 `_source`：双路="hybrid"、仅 SQL="keyword"、仅向量="semantic"
    """
    scores: dict = {}
    merged: dict = {}

    # 仅 SQL 命中先落盘（保留完整字段），双路命中时以 SQL 行为主体
    for rank, item in enumerate(sql_results, 1):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank)
        merged.setdefault(cid, dict(item))

    for rank, item in enumerate(vector_results, 1):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank)
        merged.setdefault(cid, dict(item))

    sql_ids = {r["id"] for r in sql_results}
    vec_ids = {r["id"] for r in vector_results}
    for cid, d in merged.items():
        if cid in sql_ids and cid in vec_ids:
            d["_source"] = "hybrid"
        elif cid in sql_ids:
            d["_source"] = "keyword"
        else:
            d["_source"] = "semantic"

    return sorted(merged.values(), key=lambda d: scores[d["id"]], reverse=True)
