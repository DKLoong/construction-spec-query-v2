"""混合搜索编排：LIKE 模糊搜索 + LanceDB 向量语义"""
import logging
import time
from collections import OrderedDict
from app.models import SearchQuery
from app.database import get_db
from app.search.rerank import rerank_candidates

logger = logging.getLogger(__name__)

# LIKE 搜索取全部结果时的一次性获取上限
_FETCH_LIMIT = 10000

# CrossEncoder 精排候选上限（暂定常量，后续按实际使用体验调整）：
# RRF 融合后仅对前 TOP_N 条精排，N 之后保持 RRF 原序拼接。
# 检索页展示全量结果、不做条数限制，精排只作用于头部以控耗时。
# 2026-08-26 实测：50 候选精排耗时明显（数秒级），降为 20。
_RERANK_TOP_N = 20

# 混合搜索结果序列缓存：查询指纹 → (时间戳, 精排后完整序列)。
# 每次 /search（含翻页）都会走本函数，RRF 融合 + CrossEncoder 精排是秒级
# 瓶颈；缓存让同查询翻页/往前翻直接从缓存切片，零重算、零精排。
# LRU + TTL：数据变更由 TTL 兜底 + 关键变更点显式调用 clear_search_cache。
_CACHE_MAXSIZE = 8
_CACHE_TTL = 60
_search_cache: "OrderedDict[tuple, tuple[float, list[dict]]]" = OrderedDict()


def _cache_key(query: SearchQuery) -> tuple:
    """查询指纹：页码/每页条数不影响融合结果，故不纳入 key。

    含 DATABASE_PATH 防止不同数据库（测试用临时库）共享缓存串数据。
    **必须函数内延迟导入**：顶层 `from app.database import DATABASE_PATH`
    是值绑定，monkeypatch 换库后读不到最新值，会导致跨库缓存污染。
    """
    from app.database import DATABASE_PATH
    return (
        DATABASE_PATH,
        query.keyword, query.dim1_hierarchy, query.dim1_nature, query.dim2_stage,
        query.dim3_usage, query.dim4_specialty, query.dim5_location, query.dim6_material,
        query.include_non_clause,
    )


def clear_search_cache() -> None:
    """清空混合搜索结果缓存（导入/删除/重打标等数据变更后调用）"""
    _search_cache.clear()


def hybrid_search(query: SearchQuery) -> tuple[list[dict], int]:
    """混合搜索：SQL LIKE 模糊匹配 + 向量语义补充，合并去重后分页"""
    from app.search.sql_search import search_clauses  # 延迟导入避免循环依赖

    keyword = (query.keyword or "").strip()

    # ── 0. 结果序列缓存：同查询翻页/往前翻直接命中，跳过融合与精排 ──
    key = _cache_key(query)
    now = time.time()
    cached = _search_cache.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL:
        # 命中：LRU 保活 + 刷新时间戳（活跃查询不因固定窗口过期）
        _search_cache.move_to_end(key)
        merged = cached[1]
        _search_cache[key] = (now, merged)
    else:
        # ── 1. SQL LIKE 搜索（获取全部结果，不做分页） ──
        sql_query = query.model_copy()
        sql_query.keyword = keyword
        sql_query.page = 1
        sql_query.per_page = _FETCH_LIMIT  # 先取全部，在合并后统一分页

        sql_results, _sql_total = search_clauses(sql_query)

        # ── 2. 向量搜索（语义匹配） ──
        vector_raw = []
        if keyword:
            try:
                from app.search.vector_search import VectorStore
                vs = VectorStore()
                vector_raw = vs.search(keyword, top_k=20)
            except (ImportError, OSError, RuntimeError, ValueError) as e:
                logger.warning("向量搜索不可用，降级为仅 LIKE 搜索: %s", e)

        # ── 3. 向量候选过滤（L2 < 阈值，含与 SQL 重叠的，全部参与 RRF） ──
        # L2 距离阈值（嵌入向量已归一化，BGE 模型 normalize_embeddings=True）：
        #   L2 < 1.0  → 余弦相似度 > 0.5，视为语义相关
        #   L2 >= 1.0 → 余弦相似度 ≤ 0.5，视为噪音（正交或相反方向）
        _VECTOR_THRESHOLD = 1.0
        dist_map = {}
        for v in vector_raw:
            dist = v.get("_distance", 0)
            if dist < _VECTOR_THRESHOLD:
                dist_map[v["clause_id"]] = dist

        vector_results = []
        if dist_map:
            clause_ids = list(dist_map.keys())
            with get_db() as conn:
                # 构建维度筛选条件（向量结果也需应用维度筛选，与 SQL 层一致）
                dim_conditions = []
                dim_params = []

                # 默认隐藏非条文：向量候选回查也过滤 clause_is_non=1
                if not query.include_non_clause:
                    dim_conditions.append("c.clause_is_non = 0")

                clause_filters = {
                    "dim4_specialty": query.dim4_specialty,
                    "dim5_location": query.dim5_location,
                    "dim6_material": query.dim6_material,
                }
                for col, val in clause_filters.items():
                    if val:
                        dim_conditions.append(f"c.{col} LIKE ?")
                        dim_params.append(f"%{val}%")

                spec_filters = {
                    "dim1_hierarchy": query.dim1_hierarchy,
                    "dim1_nature": query.dim1_nature,
                    "dim2_stage": query.dim2_stage,
                    "dim3_usage": query.dim3_usage,
                }
                for col, val in spec_filters.items():
                    if val:
                        dim_conditions.append(f"s.{col} LIKE ?")
                        dim_params.append(f"%{val}%")

                dim_where = (" AND " + " AND ".join(dim_conditions)) if dim_conditions else ""

                placeholders = ",".join("?" * len(clause_ids))
                rows = conn.execute(
                    f"""SELECT c.*, s.code as spec_code, s.title as spec_title,
                               s.status as spec_status, s.dim1_nature as spec_nature
                        FROM clauses c
                        JOIN specifications s ON c.spec_id = s.id
                        WHERE c.id IN ({placeholders}){dim_where}""",
                    clause_ids + dim_params,
                ).fetchall()
                seen = set()
                for r in rows:
                    d = dict(r)
                    if d["id"] not in seen:
                        seen.add(d["id"])
                        d["_distance"] = dist_map[d["id"]]
                        vector_results.append(d)

        # 向量结果按距离升序，保证 RRF rank 语义（rank 越小距离越近）
        vector_results.sort(key=lambda d: d.get("_distance", float("inf")))

        # ── 4. RRF 融合去重 ──
        from app.search.rrf import rrf_fusion
        merged = rrf_fusion(sql_results, vector_results)

        # ── 4.5 精排前缀：对前 TOP_N 过 CrossEncoder 精排，N 之后保持 RRF 原序 ──
        # 检索页全量分页展示（不做条数限制），精排仅作用于头部候选以控耗时；
        # 无关键词（纯维度筛选/浏览全部）或候选 ≤1 时跳过精排。
        if keyword and len(merged) > 1:
            try:
                head, tail = merged[:_RERANK_TOP_N], merged[_RERANK_TOP_N:]
                ranked, _ = rerank_candidates(keyword, head)
                merged = [d for d, _ in ranked] + tail
            except Exception as e:
                logger.warning("检索精排失败，降级为 RRF 原序: %s", e)

        # 写入缓存（LRU 淘汰最旧）
        if len(_search_cache) >= _CACHE_MAXSIZE:
            _search_cache.popitem(last=False)
        _search_cache[key] = (now, merged)

    total = len(merged)

    # ── 5. 分页 ──
    per_page = min(query.per_page or 20, 100)
    page = max(query.page or 1, 1)
    start = (page - 1) * per_page
    end = start + per_page

    return merged[start:end], total
