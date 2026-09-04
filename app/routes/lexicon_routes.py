"""词库管理路由：/lexicon 三 Tab（synonym/alias/confusable）统一 CRUD + CSV 导入。"""
import csv
import io

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.lexicon.store import invalidate_lexicon_caches
from app.lexicon.validation import validate_row
from app.lexicon.store import EQUIV_KINDS, KIND_CONFUSABLE

router = APIRouter()

# kind → 文案列头（模板按此渲染，避免三套列表）
KIND_COLUMNS = {
    "synonym": ("代表词", "等价词", "等价词说明"),
    "alias": ("规范词", "俗称/变体", "别名说明"),
    "confusable": ("词 A", "词 B", "区分说明"),
}


@router.get("/lexicon")
async def lexicon_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "lexicon.html",
    })


@router.get("/lexicon/list")
async def lexicon_list(request: Request, kind: str = "alias", active: str = ""):
    from app.main import templates
    with get_db() as conn:
        if active in ("0", "1"):
            rows = conn.execute(
                "SELECT * FROM lexicon_entries WHERE kind=? AND is_active=? ORDER BY id DESC",
                (kind, int(active))).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lexicon_entries WHERE kind=? ORDER BY id DESC", (kind,)).fetchall()
    return templates.TemplateResponse(request, "partials/lexicon_list.html", {
        "rows": [dict(r) for r in rows],
        "kind": kind, "columns": KIND_COLUMNS.get(kind, KIND_COLUMNS["alias"]),
        "filter_active": active,
    })


@router.post("/lexicon/create")
async def create_lexicon(request: Request, kind: str = Form(...),
                         canonical: str = Form(""), variants: str = Form(""),
                         distinguish: str = Form("")):
    data, err = validate_row(kind, canonical, variants, distinguish)
    if err:
        return HTMLResponse(f'<p style="color:red">❌ {err}</p>', status_code=400)
    with get_db() as conn:
        # synonym/alias 组唯一（应用层）：同 kind canonical 已存在 → 拒绝并立
        if kind in EQUIV_KINDS:
            dup = conn.execute(
                "SELECT 1 FROM lexicon_entries WHERE kind=? AND canonical=?",
                (kind, data["canonical"])).fetchone()
            if dup:
                return HTMLResponse(
                    '<p style="color:orange">⚠️ 该代表词已有词条，请在其变体列补充</p>', status_code=400)
        cur = conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note)"
            " VALUES (?,?,?,?,'WebUI 新增')",
            (data["kind"], data["canonical"], data["variants"], data["distinguish"]))
    invalidate_lexicon_caches()
    if cur.rowcount == 0:
        return HTMLResponse('<p style="color:orange">⚠️ 该词条已存在（全行幂等）</p>',
                            headers={"HX-Trigger": "lexiconUpdated"})
    return HTMLResponse('<p style="color:green;margin-top:0.5rem">✅ 词条已添加</p>',
                        headers={"HX-Trigger": "lexiconUpdated"})


@router.post("/lexicon/{lid}/toggle")
async def toggle_lexicon(request: Request, lid: int):
    with get_db() as conn:
        conn.execute("UPDATE lexicon_entries SET is_active = 1 - is_active WHERE id=?", (lid,))
        row = conn.execute("SELECT * FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    invalidate_lexicon_caches()
    from app.main import templates
    return templates.TemplateResponse(request, "partials/lexicon_row.html", {
        "row": dict(row), "kind": row["kind"],
        "columns": KIND_COLUMNS.get(row["kind"], KIND_COLUMNS["alias"])})


@router.get("/lexicon/{lid}/edit")
async def edit_lexicon_form(request: Request, lid: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    from app.main import templates
    return templates.TemplateResponse(request, "partials/lexicon_edit_row.html",
                                      {"row": dict(row), "kind": row["kind"]})


@router.post("/lexicon/{lid}/edit")
async def edit_lexicon(request: Request, lid: int, canonical: str = Form(""),
                       variants: str = Form(""), distinguish: str = Form("")):
    with get_db() as conn:
        row = conn.execute("SELECT kind FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if row is None:
        return HTMLResponse("", status_code=404)
    data, err = validate_row(row["kind"], canonical, variants, distinguish)
    if err:
        return HTMLResponse(f'<p style="color:red">❌ {err}</p>', status_code=400)
    if row["kind"] in EQUIV_KINDS:
        with get_db() as conn:
            dup = conn.execute(
                "SELECT 1 FROM lexicon_entries WHERE kind=? AND canonical=? AND id<>?",
                (row["kind"], data["canonical"], lid)).fetchone()
            if dup:
                return HTMLResponse('<p style="color:orange">⚠️ 已有同代表词词条</p>', status_code=400)
    with get_db() as conn:
        conn.execute("UPDATE lexicon_entries SET canonical=?, variants=?, distinguish=? WHERE id=?",
                     (data["canonical"], data["variants"], data["distinguish"], lid))
    invalidate_lexicon_caches()
    return await lexicon_list(request, kind=row["kind"])


@router.delete("/lexicon/{lid}")
async def delete_lexicon(request: Request, lid: int):
    with get_db() as conn:
        conn.execute("DELETE FROM lexicon_entries WHERE id=?", (lid,))
    invalidate_lexicon_caches()
    return HTMLResponse("", headers={"HX-Trigger": "lexiconUpdated"})


@router.post("/lexicon/import")
async def import_lexicon(request: Request, file: UploadFile = File(...),
                         kind: str = Form("")):
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    ok = skip = fail = 0
    fails: list[str] = []
    seen = set()
    with get_db() as conn:
        for line_no, rec in enumerate(reader, start=2):
            k = (rec.get("kind") or "").strip() or kind
            data, err = validate_row(k, rec.get("canonical", ""), rec.get("variants", ""),
                                     rec.get("distinguish", ""))
            if err:
                fail += 1
                fails.append(f"第{line_no}行: {err}")
                continue
            key = (data["kind"], data["canonical"], data["variants"])
            if key in seen:
                skip += 1
                continue
            seen.add(key)
            cur = conn.execute(
                "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note)"
                " VALUES (?,?,?,?,?)",
                (data["kind"], data["canonical"], data["variants"], data["distinguish"],
                 (rec.get("note") or "").strip() or "CSV 导入"))
            if cur.rowcount == 0:
                skip += 1
            else:
                ok += 1
    invalidate_lexicon_caches()
    from app.main import templates
    return templates.TemplateResponse(request, "partials/lexicon_import_result.html",
                                      {"ok": ok, "skip": skip, "fail": fail, "fails": fails})
