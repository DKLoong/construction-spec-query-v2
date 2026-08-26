from app.database import get_db
from app.models import SearchQuery


def search_clauses(query: SearchQuery) -> tuple[list[dict], int]:
    """多维筛选 + FTS5（jieba 预分词）关键词搜索，返回 (结果列表, 总数)。

    keyword 路径：FTS5 MATCH（search_text 已含 title/content/clause_no 的 jieba
    分词）→ bm25 相关度排序，clause_no 精确/包含命中提权（兼容原字段优先级行为）。
    无关键词（纯维度筛选/浏览全部）路径不变。返回签名不变 → RRF/hybrid/QA 零改动。
    """
    with get_db() as conn:
        conditions = []
        params = []
        joins = ""  # 仅 keyword 检索时 JOIN FTS 表

        # 默认隐藏非条文（前言/条文说明等打标项）；include_non_clause=True 时放行
        if not query.include_non_clause:
            conditions.append("c.clause_is_non = 0")

        # keyword → FTS5 语义检索（jieba 切词构造 MATCH）
        if query.keyword:
            from app.search.tokenize import build_match_query
            match = build_match_query(query.keyword)
            if match:
                conditions.append("f.clauses_fts MATCH ?")
                params.append(match)
                joins = " JOIN clauses_fts f ON c.id = f.rowid"
            # match 为空（keyword 全标点无有效词）→ 不加检索条件，等价无关键词

        dim_filters = {
            "dim4_specialty": query.dim4_specialty,
            "dim5_location": query.dim5_location,
            "dim6_material": query.dim6_material,
        }
        for col, val in dim_filters.items():
            if val:
                conditions.append(f"c.{col} LIKE ?")
                params.append(f"%{val}%")

        spec_filters = {
            "dim1_hierarchy": query.dim1_hierarchy,
            "dim1_nature": query.dim1_nature,
            "dim2_stage": query.dim2_stage,
            "dim3_usage": query.dim3_usage,
        }
        for col, val in spec_filters.items():
            if val:
                conditions.append(f"s.{col} LIKE ?")
                params.append(f"%{val}%")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count_sql = f"""
            SELECT COUNT(*) FROM clauses c
            JOIN specifications s ON c.spec_id = s.id
            {joins}
            {where}
        """
        total = conn.execute(count_sql, params).fetchone()[0]

        # 排序：keyword 按 bm25 相关度（FTS5 分数越小越相关）+ clause_no 精确/包含提权；
        # 无关键词（纯维度筛选）维持导入顺序 ORDER BY c.id
        order_by = "c.id"
        order_params: list = []
        if query.keyword:
            from app.search.tokenize import build_match_query
            match = build_match_query(query.keyword)
            if match:
                kw = query.keyword.strip()
                order_by = (
                    "CASE WHEN c.clause_no = ? THEN 0 "
                    "WHEN c.clause_no LIKE ? THEN 1 "
                    "ELSE 2 END, bm25(clauses_fts)"
                )
                order_params = [kw, f"%{kw}%"]

        offset = (query.page - 1) * query.per_page
        data_sql = f"""
            SELECT c.*, s.code as spec_code, s.title as spec_title,
                   s.status as spec_status, s.dim1_nature as spec_nature
            FROM clauses c
            JOIN specifications s ON c.spec_id = s.id
            {joins}
            {where}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(
            data_sql, params + order_params + [query.per_page, offset]
        ).fetchall()
        return [dict(r) for r in rows], total
