"""混合搜索编排：LIKE 模糊搜索 + LanceDB 向量语义"""
import logging
from app.models import SearchQuery
from app.database import get_db

logger = logging.getLogger(__name__)

# LIKE 搜索取全部结果时的一次性获取上限
_FETCH_LIMIT = 10000


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

    # ── 3. 合并去重 ──
    # L2 距离阈值（嵌入向量已归一化，BGE 模型 normalize_embeddings=True）：
    #   L2 < 1.0  → 余弦相似度 > 0.5，视为语义相关
    #   L2 >= 1.0 → 余弦相似度 ≤ 0.5，视为噪音（正交或相反方向）
    _VECTOR_THRESHOLD = 1.0
    sql_ids = {r["id"] for r in sql_results}
    new_ids = []
    for v in vector_raw:
        dist = v.get("_distance", 0)
        not_in_like = v["clause_id"] not in sql_ids
        dist_ok = dist < _VECTOR_THRESHOLD
        if not_in_like and dist_ok:
            new_ids.append(v["clause_id"])

    vector_results = []
    if new_ids:
        with get_db() as conn:
            # 构建维度筛选条件（向量结果也需应用维度筛选，与 FTS5 层一致）
            dim_conditions = []
            dim_params = []

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

            placeholders = ",".join("?" * len(new_ids))
            rows = conn.execute(
                f"""SELECT c.*, s.code as spec_code, s.title as spec_title
                    FROM clauses c
                    JOIN specifications s ON c.spec_id = s.id
                    WHERE c.id IN ({placeholders}){dim_where}""",
                new_ids + dim_params,
            ).fetchall()
            seen = set()
            for r in rows:
                d = dict(r)
                if d["id"] not in seen:
                    seen.add(d["id"])
                    d["_source"] = "semantic"
                    vector_results.append(d)

    merged = sql_results + vector_results
    total = len(merged)

    # ── 4. 分页 ──
    per_page = min(query.per_page or 20, 100)
    page = max(query.page or 1, 1)
    start = (page - 1) * per_page
    end = start + per_page

    return merged[start:end], total
