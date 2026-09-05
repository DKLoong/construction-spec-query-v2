"""术语/分类权威词典路由：/termdict 独立页（dim4/5/6 三 Tab CRUD）。

权威标签 = 写入 clauses 的分类值；代表词/同义词面 = 识别词面（供候选与 prompt）。
写后统一 invalidate_term_cache()，并经 HX-Trigger: termdictUpdated 驱动列表自动刷新
（列表 hx-include 页面级隐藏 dimension 输入）。编辑不改 label/dimension（权威值只读，
变更走「新增 + 删除」）。
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.termdict import (invalidate_term_cache, load_active_entries,
                          upsert_term_label)
# word_conflict 是 store 模块可变全局，__init__ 再导出是导入期快照（恒 None），
# 故按模块引用读取实时属性（load_active_entries 触发加载后才被刷新）。
import app.termdict.store as termdict_store
from app.termdict.validation import validate_row

router = APIRouter()

DIM_LABELS = {"dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"}
SOURCE_TEXT = {"seed": "种子", "migrate": "迁移", "review": "人工确认", "manual": "手动"}


def _owner_conflict(conn, dimension: str, label: str, words: list[str]):
    """任一词已属于同维其它 active label → 返回 (word, owner_label)；否则 None。

    复用 store.word_conflict_owner（active 空间、label != self），与人工确认写路径
    （feedback.process_feedback）归属判定同一实现，避免两处口径漂移。
    """
    return termdict_store.word_conflict_owner(conn, dimension, label, words)


@router.get("/termdict")
async def termdict_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "termdict.html",
        "dim_labels": DIM_LABELS,
    })


@router.get("/termdict/list")
async def termdict_list(request: Request, dimension: str = "dim6"):
    from app.main import templates
    with get_db() as conn:
        load_active_entries(dimension)   # 触发加载以刷新 word_conflict
        rows = conn.execute(
            "SELECT * FROM term_labels WHERE dimension = ? ORDER BY id DESC",
            (dimension,)).fetchall()
    return templates.TemplateResponse(request, "partials/termdict_list.html", {
        "rows": [dict(r) for r in rows], "dimension": dimension,
        "dim_labels": DIM_LABELS, "source_text": SOURCE_TEXT,
        "conflict": termdict_store.word_conflict,
    })


@router.post("/termdict/create")
async def termdict_create(request: Request, dimension: str = Form("dim6"),
                          label: str = Form(""), canonical: str = Form(""),
                          aliases: str = Form(""), note: str = Form("")):
    data, err = validate_row(dimension, label, canonical, aliases, "manual", note)
    if err:
        return HTMLResponse(f'<p style="color:red">❌ {err}</p>', status_code=400)
    words = [data["canonical"], *[a for a in data["aliases"].split(",") if a.strip()]]
    with get_db() as conn:
        owner = _owner_conflict(conn, data["dimension"], data["label"], words)
        if owner:
            return HTMLResponse(
                f'<p style="color:red">❌ 词面「{owner[0]}」已属于同维度权威标签「{owner[1]}」，请勿重复归属</p>',
                status_code=400)
        exists = conn.execute(
            "SELECT is_active FROM term_labels WHERE dimension = ? AND label = ?",
            (data["dimension"], data["label"])).fetchone()
        if exists and not exists["is_active"]:
            return HTMLResponse(
                '<p style="color:red">❌ 该权威标签已停用，请先启用后再补充词面</p>',
                status_code=400)
        upsert_term_label(conn, data["dimension"], data["label"],
                          canonical=data["canonical"], aliases=data["aliases"],
                          source="manual", note=data["note"])
    invalidate_term_cache()
    msg = "✅ 权威标签已存在，同义词面已并入" if exists else "✅ 新权威标签已添加"
    return HTMLResponse(f'<p style="color:green">{msg}</p>',
                        headers={"HX-Trigger": "termdictUpdated"})


@router.post("/termdict/{tid}/toggle")
async def termdict_toggle(request: Request, tid: int):
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE term_labels SET is_active = 1 - is_active, "
            "updated_at = datetime('now','localtime') WHERE id = ?", (tid,))
    if cur.rowcount == 0:
        return HTMLResponse("", status_code=404)
    invalidate_term_cache()
    return HTMLResponse("", headers={"HX-Trigger": "termdictUpdated"})


@router.get("/termdict/{tid}/edit")
async def termdict_edit_form(request: Request, tid: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM term_labels WHERE id = ?", (tid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/termdict_edit_row.html",
                                      {"row": dict(row)})


@router.post("/termdict/{tid}/edit")
async def termdict_edit(request: Request, tid: int, canonical: str = Form(""),
                        aliases: str = Form(""), note: str = Form("")):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, dimension, label FROM term_labels WHERE id = ?", (tid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    data, err = validate_row(row["dimension"], row["label"], canonical,
                             aliases, "manual", note)
    if err:
        return HTMLResponse(f'<p style="color:red">❌ {err}</p>', status_code=400)
    words = [data["canonical"], *[a for a in data["aliases"].split(",") if a.strip()]]
    with get_db() as conn:
        owner = _owner_conflict(conn, row["dimension"], row["label"], words)
        if owner:
            return HTMLResponse(
                f'<p style="color:red">❌ 词面「{owner[0]}」已属于「{owner[1]}」</p>',
                status_code=400)
        conn.execute(
            "UPDATE term_labels SET canonical = ?, aliases = ?, note = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (data["canonical"], data["aliases"], data["note"], tid))
    invalidate_term_cache()
    return HTMLResponse("", headers={"HX-Trigger": "termdictUpdated"})


@router.delete("/termdict/{tid}")
async def termdict_delete(request: Request, tid: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM term_labels WHERE id = ?", (tid,))
    if cur.rowcount == 0:
        return HTMLResponse("", status_code=404)
    invalidate_term_cache()
    return HTMLResponse("", headers={"HX-Trigger": "termdictUpdated"})
