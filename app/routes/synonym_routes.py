"""同义词管理路由（可编辑 UI：增删 + 启禁）

同义词在规则匹配前做归一化，避免为「砼/混凝土」等大量同义词写重复规则。
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.classifier.rule_engine import clear_synonym_cache

router = APIRouter()


def _validate(source: str, target: str) -> str | None:
    """表单校验：source/target 非空、source≠target。返回错误信息或 None。"""
    if not (source or "").strip():
        return "原词不能为空"
    if not (target or "").strip():
        return "目标词不能为空"
    if source.strip() == target.strip():
        return "原词与目标词不能相同"
    return None


@router.get("/synonyms")
async def synonyms_page(request: Request):
    """同义词管理页"""
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "synonyms.html",
    })


@router.get("/synonyms/list")
async def synonyms_list(request: Request, active: str = ""):
    """同义词列表 HTML 片段（支持按 active 过滤）"""
    with get_db() as conn:
        if active in ("1", "0"):
            rows = conn.execute(
                "SELECT * FROM synonym_map WHERE is_active = ? ORDER BY id DESC",
                (int(active),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM synonym_map ORDER BY id DESC"
            ).fetchall()

    from app.main import templates
    return templates.TemplateResponse(request, "partials/synonyms_list.html", {
        "synonyms": [dict(r) for r in rows],
        "filter_active": active,
    })


@router.post("/synonyms/create")
async def create_synonym(request: Request, source: str = Form(""),
                         target: str = Form("")):
    """创建同义词映射"""
    error = _validate(source, target)
    if error:
        return HTMLResponse(f'<p style="color:red">❌ {error}</p>', status_code=400)

    source = source.strip()
    target = target.strip()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO synonym_map (source, target, is_active) VALUES (?, ?, 1)",
            (source, target),
        )
    clear_synonym_cache()

    if cur.rowcount == 0:
        return HTMLResponse(
            '<p style="color:orange">⚠️ 同义词已存在，未重复添加</p>',
            headers={"HX-Trigger": "synonymsUpdated"},
        )
    return HTMLResponse(
        f'<p style="color:green;margin-top:0.5rem">✅ 同义词已添加：{source} → {target}</p>',
        headers={"HX-Trigger": "synonymsUpdated"},
    )


@router.post("/synonyms/{synonym_id}/toggle")
async def toggle_synonym(request: Request, synonym_id: int):
    """启用/禁用同义词 — 返回更新行 HTML 片段"""
    with get_db() as conn:
        conn.execute(
            "UPDATE synonym_map SET is_active = 1 - is_active WHERE id = ?",
            (synonym_id,),
        )
        row = conn.execute(
            "SELECT * FROM synonym_map WHERE id = ?", (synonym_id,)
        ).fetchone()

    if row is None:
        return HTMLResponse("", status_code=404)
    clear_synonym_cache()

    from app.main import templates
    return templates.TemplateResponse(request, "partials/synonyms_row.html", {
        "synonym": dict(row),
    })


@router.delete("/synonyms/{synonym_id}")
async def delete_synonym(request: Request, synonym_id: int):
    """删除同义词 — 返回完整列表"""
    with get_db() as conn:
        conn.execute("DELETE FROM synonym_map WHERE id = ?", (synonym_id,))
    clear_synonym_cache()
    return await synonyms_list(request)
