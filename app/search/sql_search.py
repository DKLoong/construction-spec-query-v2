from app.database import get_db
from app.models import SearchQuery


def search_clauses(query: SearchQuery) -> tuple[list[dict], int]:
    """多维筛选 + FTS5（jieba 预分词）关键词搜索，返回 (结果列表, 总数)。

    keyword 路径：FTS5 MATCH（search_text 已含 title/content/clause_no 的 jieba
    分词）→ bm25 相关度排序 + 字段信号提权（clause_no 精确/包含、title LIKE）。
    多词 AND 无结果时降级 OR 召回（任一 token 命中），避免严格匹配查空。
    无关键词（纯维度筛选/浏览全部）路径不变。返回签名不变 → RRF/hybrid/QA 零改动。
    """
    with get_db() as conn:
        def _build(match_expr: str) -> tuple[list, list, str]:
            """按指定 MATCH 表达式构建检索条件（keyword 为空时 match_expr=''）"""
            conditions = []
            params = []
            joins = ""
            # 默认隐藏非条文（前言/条文说明等打标项）；include_non_clause=True 时放行
            if not query.include_non_clause:
                conditions.append("c.clause_is_non = 0")
            if match_expr:
                conditions.append("f.clauses_fts MATCH ?")
                params.append(match_expr)
                joins = " JOIN clauses_fts f ON c.id = f.rowid"

            def _add_multi(cond_list, params_list, col, vals):
                """同维多值 → OR 组：(col LIKE ? OR col LIKE ? ...)；空列表跳过"""
                if not vals:
                    return
                ors = []
                for v in vals:
                    ors.append(f"{col} LIKE ?")
                    params_list.append(f"%{v}%")
                cond_list.append("(" + " OR ".join(ors) + ")")

            # dim4~dim6 挂在 clauses 表，dim1~dim3 挂在 specifications 表
            _add_multi(conditions, params, "c.dim4_specialty", query.dim4_specialty)
            _add_multi(conditions, params, "c.dim5_location", query.dim5_location)
            _add_multi(conditions, params, "c.dim6_material", query.dim6_material)
            _add_multi(conditions, params, "s.dim1_hierarchy", query.dim1_hierarchy)
            _add_multi(conditions, params, "s.dim1_nature", query.dim1_nature)
            _add_multi(conditions, params, "s.dim2_stage", query.dim2_stage)
            _add_multi(conditions, params, "s.dim3_usage", query.dim3_usage)

            if query.status_filter:
                ph = ",".join("?" * len(query.status_filter))
                conditions.append(f"s.status IN ({ph})")
                params.extend(query.status_filter)
            return conditions, params, joins

        # keyword → FTS5 MATCH 查询串（jieba 切词，token 间 AND）
        from app.search.tokenize import build_match_query
        match = build_match_query(query.keyword) if query.keyword else ""

        conditions, params, joins = _build(match)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        def _count() -> int:
            return conn.execute(
                f"""SELECT COUNT(*) FROM clauses c
                    JOIN specifications s ON c.spec_id = s.id
                    {joins} {where}""",
                params,
            ).fetchone()[0]

        total = _count()

        # OR 兜底：多词 AND 无结果 → 任一 token 命中即召回（解决「I级接头强度」查空）
        if total == 0 and query.keyword and match:
            or_match = build_match_query(query.keyword, "OR")
            if or_match != match:
                conditions, params, joins = _build(or_match)
                where = "WHERE " + " AND ".join(conditions) if conditions else ""
                total = _count()

        # 排序：bm25 相关度（FTS5 分数越小越相关）+ 字段信号提权
        # （clause_no 精确 > 包含 > title 命中 > 其他）；无关键词维持导入序
        order_by = "c.id"
        order_params: list = []
        if query.keyword and match:
            kw = query.keyword.strip()
            order_by = (
                "CASE WHEN c.clause_no = ? THEN 0 "
                "WHEN c.clause_no LIKE ? THEN 1 "
                "WHEN c.title LIKE ? THEN 2 "
                "ELSE 3 END, bm25(clauses_fts)"
            )
            order_params = [kw, f"%{kw}%", f"%{kw}%"]

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
