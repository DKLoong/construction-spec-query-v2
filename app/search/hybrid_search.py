"""混合搜索编排：LIKE 模糊搜索 + LanceDB 向量语义"""
from app.models import SearchQuery
from app.database import get_db


def hybrid_search(query: SearchQuery) -> tuple[list[dict], int]:
    """混合搜索：SQL LIKE 模糊匹配 + 向量语义补充，合并去重后分页"""
    from app.search.sql_search import search_clauses  # 延迟导入避免循环依赖

    keyword = (query.keyword or "").strip()

    # ── 1. SQL LIKE 搜索（获取全部结果，不做分页） ──
    sql_query = query.model_copy()
    sql_query.keyword = keyword
    sql_query.page = 1
    sql_query.per_page = 10000  # 先取全部，在合并后统一分页

    sql_results, sql_total = search_clauses(sql_query)

    # ── 2. 向量搜索（语义匹配） ──
    vector_raw = []
    if keyword:
        try:
            from app.search.vector_search import VectorStore
            vs = VectorStore()
            vector_raw = vs.search(keyword, top_k=50)
        except Exception:
            pass  # 向量搜索不可用时静默降级

    # ── 3. 合并去重 ──
    sql_ids = {r["id"] for r in sql_results}
    new_ids = [v["clause_id"] for v in vector_raw if v["clause_id"] not in sql_ids]

    vector_results = []
    if new_ids:
        with get_db() as conn:
            placeholders = ",".join("?" * len(new_ids))
            rows = conn.execute(
                f"""SELECT c.*, s.code as spec_code, s.title as spec_title
                    FROM clauses c
                    JOIN specifications s ON c.spec_id = s.id
                    WHERE c.id IN ({placeholders})""",
                new_ids,
            ).fetchall()
            seen = set()
            for r in rows:
                d = dict(r)
                if d["id"] not in seen:
                    seen.add(d["id"])
                    d["_source"] = "semantic"
                    vector_results.append(d)

    merged = list(sql_results) + vector_results
    total = len(merged)

    # ── 4. 分页 ──
    per_page = min(query.per_page or 20, 100)
    page = max(query.page or 1, 1)
    start = (page - 1) * per_page
    end = start + per_page

    return merged[start:end], total
