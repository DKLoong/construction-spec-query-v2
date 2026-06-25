"""混合搜索编排：FTS5 全文 + LanceDB 向量语义"""
import re
from app.models import SearchQuery
from app.database import get_db


# FTS5 语法特殊字符（中文工程术语中不会出现，直接移除）
_FTS5_SPECIAL = re.compile(r'[*"(){}\[\]^~:+\-=/,]')


def _sanitize_fts5(keyword: str) -> str:
    """移除 FTS5 特殊字符，避免语法错误"""
    return _FTS5_SPECIAL.sub(' ', keyword).strip()


def hybrid_search(query: SearchQuery) -> tuple[list[dict], int]:
    """混合搜索：FTS5 精确命中 + 向量语义补充，合并去重后分页"""
    from app.search.sql_search import search_clauses  # 延迟导入避免循环依赖

    keyword = (query.keyword or "").strip()

    # ── 1. FTS5 搜索（获取全部结果，不做分页） ──
    # 清理 FTS5 特殊字符
    clean_keyword = _sanitize_fts5(keyword) if keyword else ""
    fts5_query = query.model_copy()
    fts5_query.keyword = clean_keyword
    fts5_query.page = 1
    fts5_query.per_page = 10000  # 先取全部，在合并后统一分页

    fts5_results, fts5_total = search_clauses(fts5_query)

    # ── 2. 向量搜索（语义匹配） ──
    vector_raw = []
    if clean_keyword:
        try:
            from app.search.vector_search import VectorStore
            vs = VectorStore()
            vector_raw = vs.search(clean_keyword, top_k=50)
        except Exception:
            pass  # 向量搜索不可用时静默降级

    # ── 3. 合并去重 ──
    fts5_ids = {r["id"] for r in fts5_results}
    new_ids = [v["clause_id"] for v in vector_raw if v["clause_id"] not in fts5_ids]

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

    merged = list(fts5_results) + vector_results
    total = len(merged)

    # ── 4. 分页 ──
    per_page = min(query.per_page or 20, 100)
    page = max(query.page or 1, 1)
    start = (page - 1) * per_page
    end = start + per_page

    return merged[start:end], total
