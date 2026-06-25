from app.database import get_db
from app.models import SearchQuery


def search_clauses(query: SearchQuery) -> tuple[list[dict], int]:
    """多维筛选 + FTS5 关键词搜索，返回 (结果列表, 总数)"""
    with get_db() as conn:
        conditions = []
        params = []

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

        offset = (query.page - 1) * query.per_page
        data_sql = f"""
            SELECT c.*, s.code as spec_code, s.title as spec_title
            FROM clauses c
            JOIN specifications s ON c.spec_id = s.id
            {where}
            ORDER BY c.id
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(data_sql, params + [query.per_page, offset]).fetchall()
        return [dict(r) for r in rows], total
