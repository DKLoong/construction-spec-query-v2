"""搜索路由"""
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from app.models import SearchQuery
from app.database import get_db

router = APIRouter()


def _safe_summary(content: str, limit: int = 200) -> str:
    """安全截断条文摘要：保证 LaTeX 公式闭合（$ 成对），避免前端渲染配对错乱

    摘要可能落在公式中间（如 "$ 5 \\, m"），奇数个 $ 会让前端 KaTeX 把后续
    文本误当公式；此处回退到最后一个 $ 之前截断。
    """
    if len(content) <= limit:
        return content
    s = content[:limit]
    if s.count("$") % 2 == 1:
        idx = s.rfind("$")
        if idx > 0:
            s = s[:idx]
    return s


@router.get("/search")
async def search(
    request: Request,
    keyword: str = Query("", max_length=200),
    # 维度筛选同维多选：list[str] = Query([])，重复参数（?dim4_specialty=a&dim4_specialty=b）聚合为列表
    dim1_hierarchy: list[str] = Query([]),
    dim1_nature: list[str] = Query([]),
    dim2_stage: list[str] = Query([]),
    dim3_usage: list[str] = Query([]),
    dim4_specialty: list[str] = Query([]),
    dim5_location: list[str] = Query([]),
    dim6_material: list[str] = Query([]),
    page: int = Query(1),
    page_size: int = Query(20),
    all: bool = Query(False),
    include_non_clause: bool = Query(False),
    ce_rerank: bool = Query(False),
    status_filter: str = Query(""),
):
    """混合搜索：关键词 + 六维筛选 + 分页

    all=1 表示用户主动取消所有筛选后的显式全量查询：无关键词无筛选时
    仍返回全部条文（而非空搜索提示），供前端「取消所有筛选」后浏览全部。
    """
    from app.search.hybrid_search import hybrid_search

    effective_page_size = min(page_size, 100)

    sq = SearchQuery(
        keyword=keyword,
        dim1_hierarchy=dim1_hierarchy,
        dim1_nature=dim1_nature,
        dim2_stage=dim2_stage,
        dim3_usage=dim3_usage,
        dim4_specialty=dim4_specialty,
        dim5_location=dim5_location,
        dim6_material=dim6_material,
        page=page,
        per_page=effective_page_size,
        include_non_clause=include_non_clause,
        ce_rerank=ce_rerank,
        status_filter=[s.strip() for s in status_filter.split(",") if s.strip()],
    )

    # 无关键词 + 无任何维度筛选 → 返回提示（除非 all=1 显式要求全量）
    # 维度字段已是 list，用 any 同时覆盖空字符串与空列表；
    # 注意不能用 all()：函数签名中的 all: bool 参数会遮蔽内建 all
    all_empty = not any([
        keyword,
        *[getattr(sq, f) for f in (
            'dim1_hierarchy', 'dim1_nature', 'dim2_stage', 'dim3_usage',
            'dim4_specialty', 'dim5_location', 'dim6_material',
        )],
    ])
    if all_empty and not all:
        from app.main import templates
        return templates.TemplateResponse(request, "partials/result_list.html", {
            "results": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "keyword": "",
            "dim1_hierarchy": [],
            "dim1_nature": [],
            "dim2_stage": [],
            "dim3_usage": [],
            "dim4_specialty": [],
            "dim5_location": [],
            "dim6_material": [],
            "include_non_clause": include_non_clause,
            "ce_rerank": ce_rerank,
            "status_filter": status_filter,
            "empty_search": True,
        })

    results, total = hybrid_search(sq)

    # 为每条结果生成安全摘要（保证 LaTeX 公式闭合），供列表前端渲染
    for r in results:
        r["summary"] = _safe_summary(r.get("content", ""), 200)

    # 始终返回 result_list.html（含 #search-results 包装）
    # 分页使用 hx-swap="outerHTML" 替换整个 #search-results div，天然避免嵌套
    from app.main import templates
    return templates.TemplateResponse(request, "partials/result_list.html", {
        "results": results,
        "total": total,
        "page": page,
        "page_size": effective_page_size,
        "keyword": keyword,
        "dim1_hierarchy": dim1_hierarchy,
        "dim1_nature": dim1_nature,
        "dim2_stage": dim2_stage,
        "dim3_usage": dim3_usage,
        "dim4_specialty": dim4_specialty,
        "dim5_location": dim5_location,
        "dim6_material": dim6_material,
        "include_non_clause": include_non_clause,
        "ce_rerank": ce_rerank,
        "status_filter": status_filter,
    })


@router.get("/clause/{clause_id}")
async def clause_detail(request: Request, clause_id: int):
    """条文详情"""
    with get_db() as conn:
        clause = conn.execute(
            """SELECT c.*, s.code as spec_code, s.title as spec_title,
                      s.status as spec_status, s.replace_by_spec_id,
                      r.code as replace_by_code, r.title as replace_by_title
               FROM clauses c
               JOIN specifications s ON c.spec_id = s.id
               LEFT JOIN specifications r ON r.id = s.replace_by_spec_id
               WHERE c.id = ?""",
            (clause_id,),
        ).fetchone()

    if not clause:
        return JSONResponse({"detail": "条文不存在"}, status_code=404)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/clause_detail.html", {
        "clause": dict(clause),
    })
