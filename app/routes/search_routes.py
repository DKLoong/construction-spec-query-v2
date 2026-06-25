"""搜索路由"""
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from app.models import SearchQuery
from app.database import get_db

router = APIRouter()


@router.get("/search")
async def search(
    request: Request,
    keyword: str = Query(""),
    dim1_hierarchy: str = Query(""),
    dim1_nature: str = Query(""),
    dim2_stage: str = Query(""),
    dim3_usage: str = Query(""),
    dim4_specialty: str = Query(""),
    dim5_location: str = Query(""),
    dim6_material: str = Query(""),
    page: int = Query(1),
    page_size: int = Query(20),
):
    """混合搜索：关键词 + 六维筛选 + 分页"""
    from app.search.hybrid_search import hybrid_search

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
        per_page=min(page_size, 100),
    )

    results, total = hybrid_search(sq)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/result_list.html", {
        "results": results,
        "total": total,
        "page": page,
        "page_size": min(page_size, 100),  # 与 per_page 上限一致，避免分页计算错误
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
