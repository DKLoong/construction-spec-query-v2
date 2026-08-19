"""规范管理 & 条文 CRUD 路由"""
import logging
from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════
# 规范管理
# ═══════════════════════════════════════════

@router.get("/specs")
async def specs_page(request: Request):
    """规范管理页"""
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/specs_list.html",
    })


@router.get("/specs/list")
async def specs_list(request: Request):
    """规范列表 HTML 片段"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.*, COUNT(c.id) as clause_count
               FROM specifications s
               LEFT JOIN clauses c ON c.spec_id = s.id
               GROUP BY s.id
               ORDER BY s.created_at DESC"""
        ).fetchall()

    from app.main import templates
    return templates.TemplateResponse(request, "partials/specs_table.html", {
        "specs": [dict(r) for r in rows],
    })


@router.delete("/specs/{spec_id}")
async def delete_spec(request: Request, spec_id: int):
    """删除规范（级联删除条文、队列、FTS5、向量）"""
    # 1. 收集该规范下所有条文 ID（用于向量清理）
    clause_ids = []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, clause_no, content, spec_id FROM clauses WHERE spec_id = ?",
            (spec_id,),
        ).fetchall()
        clause_ids = [r["id"] for r in rows]

    # 2. 清理 LanceDB 向量
    try:
        from app.search.vector_search import VectorStore
        vs = VectorStore()
        for cid in clause_ids:
            try:
                vs.delete_clause(cid)
            except Exception:
                pass
    except Exception as e:
        logger.warning("向量清理失败（可能表不存在）: %s", e)

    # 3. 删除规范（FK CASCADE 自动清理 clauses、queue、FTS5）
    with get_db() as conn:
        conn.execute("DELETE FROM specifications WHERE id = ?", (spec_id,))

    # OOB swap 清空仍在显示该规范条文/分类编辑的区域（详情区不被替换时同步刷新）
    return HTMLResponse(
        '<div id="clause-detail-area" hx-swap-oob="true"></div>'
        '<div id="spec-class-area" hx-swap-oob="true"></div>'
    )


# ═══════════════════════════════════════════
# 条文管理
# ═══════════════════════════════════════════

@router.get("/specs/{spec_id}/clauses")
async def spec_clauses(request: Request, spec_id: int):
    """条文列表"""
    with get_db() as conn:
        spec = conn.execute(
            "SELECT * FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()
        if not spec:
            return HTMLResponse("<p>规范不存在</p>", status_code=404)

        clauses = conn.execute(
            "SELECT * FROM clauses WHERE spec_id = ? ORDER BY id",
            (spec_id,),
        ).fetchall()

    from app.main import templates
    return templates.TemplateResponse(request, "partials/clauses_table.html", {
        "spec": dict(spec),
        "clauses": [dict(c) for c in clauses],
    })


@router.get("/specs/{spec_id}/imgs/{filename}")
async def spec_image(request: Request, spec_id: int, filename: str):
    """服务条文 content 中引用的图片（位于 spec.output_dir/imgs/）

    OCR 图片经导入确认时复制到 spec.output_dir/imgs/；条文页渲染 content 时把
    相对引用 imgs/xxx.jpg 改写为 /specs/{spec_id}/imgs/xxx.jpg 后由本路由返回。
    """
    from fastapi.responses import FileResponse

    with get_db() as conn:
        row = conn.execute(
            "SELECT output_dir FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()

    if not row or not row["output_dir"]:
        return JSONResponse({"detail": "规范不存在或无图片目录"}, status_code=404)

    # 仅取文件名，防路径穿越
    safe_name = Path(filename).name
    img_path = Path(row["output_dir"]) / "imgs" / safe_name
    if not img_path.is_file():
        return JSONResponse({"detail": "图片不存在"}, status_code=404)
    return FileResponse(str(img_path))


@router.get("/specs/{spec_id}/clauses/{clause_id}/edit")
async def edit_clause_form(request: Request, spec_id: int, clause_id: int):
    """行内编辑表单"""
    with get_db() as conn:
        clause = conn.execute(
            "SELECT * FROM clauses WHERE id = ? AND spec_id = ?",
            (clause_id, spec_id),
        ).fetchone()
        if not clause:
            return HTMLResponse("<tr><td colspan='4'>条文不存在</td></tr>", status_code=404)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/clause_edit_form.html", {
        "clause": dict(clause),
        "spec_id": spec_id,
    })


@router.get("/specs/{spec_id}/clauses/{clause_id}/edit-page")
async def clause_edit_page(request: Request, spec_id: int, clause_id: int):
    """两栏条文编辑页（左编辑右实时预览），仿 OCR 审查页布局

    独立整页跳转（hide_tree 全宽），确认/取消后 history.back() 返回原列表页，
    滚动位置由 specs_list 页面的 pageshow 逻辑恢复。
    """
    with get_db() as conn:
        clause = conn.execute(
            """SELECT c.*, s.code as spec_code, s.title as spec_title
               FROM clauses c JOIN specifications s ON c.spec_id = s.id
               WHERE c.id = ? AND c.spec_id = ?""",
            (clause_id, spec_id),
        ).fetchone()
        if not clause:
            return HTMLResponse("<p>条文不存在</p>", status_code=404)

    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/clause_edit_page.html",
        "clause": dict(clause),
        "spec_id": spec_id,
        "hide_tree": True,  # 全宽两栏显示
    })


@router.put("/specs/{spec_id}/clauses/{clause_id}")
async def update_clause(
    request: Request,
    spec_id: int,
    clause_id: int,
    clause_no: str = Form(""),
    title: str = Form(""),
    content: str = Form(""),
    dim4_specialty: str = Form(""),
    dim5_location: str = Form(""),
    dim6_material: str = Form(""),
):
    """更新条文（含分类字段）"""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM clauses WHERE id = ? AND spec_id = ?",
            (clause_id, spec_id),
        ).fetchone()
        if not existing:
            return JSONResponse({"detail": "条文不存在"}, status_code=404)

        conn.execute(
            """UPDATE clauses SET clause_no = ?, title = ?, content = ?,
               dim4_specialty = ?, dim5_location = ?, dim6_material = ?
               WHERE id = ?""",
            (clause_no, title, content,
             dim4_specialty, dim5_location, dim6_material,
             clause_id),
        )

        # 重索引向量
        try:
            from app.search.vector_search import VectorStore
            spec = conn.execute(
                "SELECT code, title FROM specifications WHERE id = ?", (spec_id,)
            ).fetchone()
            vs = VectorStore()
            embed_text = f"{spec['code'] or ''} {spec['title'] or ''} [{clause_no}] {title or ''} {content}"
            vs.index_clause(clause_id, spec_id, embed_text)
        except Exception as e:
            logger.warning("向量重索引失败: %s", e)

        updated = dict(existing)
        updated.update({
            "clause_no": clause_no, "title": title, "content": content,
            "dim4_specialty": dim4_specialty, "dim5_location": dim5_location,
            "dim6_material": dim6_material,
        })

    from app.main import templates
    return templates.TemplateResponse(request, "partials/clause_edit_form.html", {
        "clause": updated,
        "spec_id": spec_id,
        "saved": True,
    })


@router.delete("/specs/{spec_id}/clauses/{clause_id}")
async def delete_clause(request: Request, spec_id: int, clause_id: int):
    """删除单条条文"""
    # 1. 清理向量
    try:
        from app.search.vector_search import VectorStore
        vs = VectorStore()
        vs.delete_clause(clause_id)
    except Exception:
        pass

    # 2. 删除条文（FK CASCADE 自动清理 queue，FTS5 触发器自动同步）
    with get_db() as conn:
        conn.execute(
            "DELETE FROM clauses WHERE id = ? AND spec_id = ?",
            (clause_id, spec_id),
        )
        # 更新规范条文计数
        conn.execute(
            """UPDATE specifications
               SET clause_count = (SELECT COUNT(*) FROM clauses WHERE spec_id = ?)
               WHERE id = ?""",
            (spec_id, spec_id),
        )

    return HTMLResponse("")


# ═══════════════════════════════════════════
# 分类编辑
# ═══════════════════════════════════════════

@router.get("/specs/{spec_id}/edit-class")
async def edit_spec_class_form(request: Request, spec_id: int):
    """规范分类编辑表单"""
    with get_db() as conn:
        spec = conn.execute(
            "SELECT * FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()
        if not spec:
            return HTMLResponse("<p>规范不存在</p>", status_code=404)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/spec_class_edit.html", {
        "spec": dict(spec),
    })


@router.put("/specs/{spec_id}/class")
async def update_spec_class(
    request: Request,
    spec_id: int,
    dim2_stage: str = Form(""),
    dim3_usage: str = Form(""),
):
    """更新规范分类 (dim2/dim3)"""
    with get_db() as conn:
        spec = conn.execute(
            "SELECT * FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()
        if not spec:
            return JSONResponse({"detail": "规范不存在"}, status_code=404)

        conn.execute(
            """UPDATE specifications SET dim2_stage = ?, dim3_usage = ?
               WHERE id = ?""",
            (dim2_stage, dim3_usage, spec_id),
        )

    from app.main import templates
    updated = dict(spec)
    updated["dim2_stage"] = dim2_stage
    updated["dim3_usage"] = dim3_usage
    return templates.TemplateResponse(request, "partials/spec_class_edit.html", {
        "spec": updated,
        "saved": True,
    })


@router.get("/specs/{spec_id}/clauses/{clause_id}/edit-class")
async def edit_clause_class_form(request: Request, spec_id: int, clause_id: int):
    """条文分类编辑表单"""
    with get_db() as conn:
        clause = conn.execute(
            "SELECT * FROM clauses WHERE id = ? AND spec_id = ?",
            (clause_id, spec_id),
        ).fetchone()
        if not clause:
            return HTMLResponse("<tr><td colspan='5'>条文不存在</td></tr>", status_code=404)

    from app.main import templates
    return templates.TemplateResponse(request, "partials/clause_class_edit.html", {
        "clause": dict(clause),
        "spec_id": spec_id,
    })


@router.put("/specs/{spec_id}/clauses/{clause_id}/class")
async def update_clause_class(
    request: Request,
    spec_id: int,
    clause_id: int,
    dim4_specialty: str = Form(""),
    dim5_location: str = Form(""),
    dim6_material: str = Form(""),
):
    """仅更新条文分类 (dim4/dim5/dim6)"""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM clauses WHERE id = ? AND spec_id = ?",
            (clause_id, spec_id),
        ).fetchone()
        if not existing:
            return JSONResponse({"detail": "条文不存在"}, status_code=404)

        conn.execute(
            """UPDATE clauses SET dim4_specialty = ?, dim5_location = ?, dim6_material = ?
               WHERE id = ?""",
            (dim4_specialty, dim5_location, dim6_material, clause_id),
        )

    from app.main import templates
    updated = dict(existing)
    updated.update({"dim4_specialty": dim4_specialty, "dim5_location": dim5_location,
                    "dim6_material": dim6_material})
    return templates.TemplateResponse(request, "partials/clause_class_edit.html", {
        "clause": updated,
        "spec_id": spec_id,
        "saved": True,
    })
