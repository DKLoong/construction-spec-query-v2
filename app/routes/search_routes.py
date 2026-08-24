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
    dim1_hierarchy: str = Query("", max_length=100),
    dim1_nature: str = Query("", max_length=100),
    dim2_stage: str = Query("", max_length=100),
    dim3_usage: str = Query("", max_length=100),
    dim4_specialty: str = Query("", max_length=100),
    dim5_location: str = Query("", max_length=100),
    dim6_material: str = Query("", max_length=100),
    page: int = Query(1),
    page_size: int = Query(20),
    all: bool = Query(False),
    include_non_clause: bool = Query(False),
):
    """混合搜索：关键词 + 六维筛选 + 分页

    all=1 表示用户主动取消所有筛选后的显式全量查询：无关键词无筛选时
    仍返回全部条文（而非空搜索提示），供前端「取消所有筛选」后浏览全部。
    """
    from app.search.hybrid_search import hybrid_search

    # 无关键词 + 无任何维度筛选 → 返回提示（除非 all=1 显式要求全量）
    all_empty = not any([
        keyword, dim1_hierarchy, dim1_nature, dim2_stage,
        dim3_usage, dim4_specialty, dim5_location, dim6_material,
    ])
    if all_empty and not all:
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
            "include_non_clause": include_non_clause,
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
        include_non_clause=include_non_clause,
    )

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
