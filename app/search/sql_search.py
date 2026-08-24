from app.database import get_db
from app.models import SearchQuery


def search_clauses(query: SearchQuery) -> tuple[list[dict], int]:
    """多维筛选 + LIKE 关键词搜索，返回 (结果列表, 总数)"""
    with get_db() as conn:
        conditions = []
        params = []

        # 默认隐藏非条文（前言/条文说明等打标项）；include_non_clause=True 时放行
        if not query.include_non_clause:
            conditions.append("c.clause_is_non = 0")

        if query.keyword:
            conditions.append(
                "(c.content LIKE ? OR c.title LIKE ? OR c.clause_no LIKE ?)"
            )
            kw = f"%{query.keyword}%"
            params.extend([kw, kw, kw])

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
            {where}
        """
        total = conn.execute(count_sql, params).fetchone()[0]

        # 字段优先级打分排序：关键词命中字段越精确排名越靠前；
        # 无关键词（纯维度筛选）时维持导入顺序 ORDER BY c.id
        order_by = "c.id"
        order_params: list = []
        if query.keyword:
            kw = f"%{query.keyword}%"
            order_by = (
                "CASE WHEN c.clause_no = ? THEN 3 "
                "WHEN c.clause_no LIKE ? THEN 2.5 "
                "WHEN c.title LIKE ? THEN 2 "
                "WHEN c.content LIKE ? THEN 1 "
                "ELSE 0 END DESC, c.clause_no ASC"
            )
            order_params = [query.keyword, kw, kw, kw]

        offset = (query.page - 1) * query.per_page
        data_sql = f"""
            SELECT c.*, s.code as spec_code, s.title as spec_title
            FROM clauses c
            JOIN specifications s ON c.spec_id = s.id
            {where}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(
            data_sql, params + order_params + [query.per_page, offset]
        ).fetchall()
        return [dict(r) for r in rows], total
