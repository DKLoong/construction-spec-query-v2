"""混合搜索编排：LIKE 模糊搜索 + LanceDB 向量语义"""
import logging
from app.models import SearchQuery
from app.database import get_db
from app.search.rerank import rerank_candidates

logger = logging.getLogger(__name__)

# LIKE 搜索取全部结果时的一次性获取上限
_FETCH_LIMIT = 10000

# CrossEncoder 精排候选上限（暂定常量，后续按实际使用体验调整）：
# RRF 融合后仅对前 TOP_N 条精排，N 之后保持 RRF 原序拼接。
# 检索页展示全量结果、不做条数限制，精排只作用于头部以控耗时。
_RERANK_TOP_N = 50


def hybrid_search(query: SearchQuery) -> tuple[list[dict], int]:
    """混合搜索：SQL LIKE 模糊匹配 + 向量语义补充，合并去重后分页"""
    from app.search.sql_search import search_clauses  # 延迟导入避免循环依赖

    keyword = (query.keyword or "").strip()

    # ── 1. SQL LIKE 搜索（获取全部结果，不做分页） ──
    sql_query = query.model_copy()
    sql_query.keyword = keyword
    sql_query.page = 1
    sql_query.per_page = _FETCH_LIMIT  # 先取全部，在合并后统一分页

    sql_results, sql_total = search_clauses(sql_query)

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
    total = len(merged)

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

    # ── 5. 分页 ──
    per_page = min(query.per_page or 20, 100)
    page = max(query.page or 1, 1)
    start = (page - 1) * per_page
    end = start + per_page

    return merged[start:end], total
