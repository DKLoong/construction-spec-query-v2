"""搜索路由"""
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from app.models import SearchQuery
from app.database import get_db

router = APIRouter()


@router.get("/search")
async def search(
    request: Request,
    keyword: str = Query("", max_length=200),
    dim1_hierarchy: str = Query("", max_length=100),
    dim1_nature: str = Query("", max_length=100),
    dim2_stage: str = Query("", max_length=100),
    dim3_usage: str = Query("", max_length=100),
    dim4_specialty: str = Query("", max_length=100),
    dim5_location: str = Query("", max_length=100),
    dim6_material: str = Query("", max_length=100),
    page: int = Query(1),
    page_size: int = Query(20),
):
    """混合搜索：关键词 + 六维筛选 + 分页"""
    from app.search.hybrid_search import hybrid_search

    # 无关键词 + 无任何维度筛选 → 返回提示
    all_empty = not any([
        keyword, dim1_hierarchy, dim1_nature, dim2_stage,
        dim3_usage, dim4_specialty, dim5_location, dim6_material,
    ])
    if all_empty:
        from app.main import templates
        return templates.TemplateResponse(request, "partials/result_list.html", {
            "results": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "keyword": "",
            "dim1_hierarchy": "",
            "dim1_nature": "",
            "dim2_stage": "",
            "dim3_usage": "",
            "dim4_specialty": "",
            "dim5_location": "",
            "dim6_material": "",
            "empty_search": True,
        })

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
    )

    results, total = hybrid_search(sq)

    # HTMX 请求（翻页）：返回不带外层 #search-results 包装的内容，避免嵌套
    # 首次加载（dispatchSearch / 浏览器直接访问）：返回完整包装
    is_htmx = request.headers.get("HX-Request") == "true"
    template_name = "partials/result_content.html" if is_htmx else "partials/result_list.html"

    from app.main import templates
    return templates.TemplateResponse(request, template_name, {
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
    })


@router.get("/clause/{clause_id}")
async def clause_detail(request: Request, clause_id: int):
    """条文详情"""
    with get_db() as conn:
        clause = conn.execute(
            """SELECT c.*, s.code as spec_code, s.title as spec_title
               FROM clauses c
               JOIN specifications s ON c.spec_id = s.id
               WHERE c.id = ?""",
            (clause_id,),
        ).fetchone()

    if not clause:
        return JSONResponse({"detail": "条文不存在"}, status_code=404)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/clause_detail.html", {
        "clause": dict(clause),
    })
