"""词库管理路由：/lexicon 三 Tab（synonym/alias/confusable）统一 CRUD + CSV 导入。"""
import csv
import io

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.lexicon.store import invalidate_lexicon_caches
from app.lexicon.validation import validate_row
from app.lexicon.store import EQUIV_KINDS

router = APIRouter()


def _find_equiv_group(conn, kind: str, canonical: str):
    """查同 (kind, canonical) 的已有 synonym/alias 组行（应用层组唯一约束查询）。

    create/edit/import 三路径共用，杜绝「同 canonical 已有行」判定逻辑再次漂移。
    返回 sqlite3.Row（含 id/variants）或 None。confusable 走全行幂等，不经此 helper。
    """
    return conn.execute(
        "SELECT id, variants FROM lexicon_entries WHERE kind=? AND canonical=? "
        "ORDER BY id LIMIT 1", (kind, canonical)).fetchone()

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
        # synonym/alias 组唯一（应用层）：同 kind canonical 已存在 → 拒绝并立。
        # 用 validate_row 返回的 data["kind"]（已 strip），不用原始 Form kind。
        if data["kind"] in EQUIV_KINDS:
            if _find_equiv_group(conn, data["kind"], data["canonical"]):
                return HTMLResponse(
                    '<p style="color:orange">⚠️ 该代表词已有词条，请在其变体列补充</p>', status_code=400)
        cur = conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note,updated_at)"
            " VALUES (?,?,?,?,'WebUI 新增',datetime('now','localtime'))",
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
        conn.execute("UPDATE lexicon_entries SET is_active = 1 - is_active, "
                     "updated_at = datetime('now','localtime') WHERE id=?", (lid,))
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
            dup = _find_equiv_group(conn, row["kind"], data["canonical"])
            if dup and dup["id"] != lid:
                return HTMLResponse('<p style="color:orange">⚠️ 已有同代表词词条</p>', status_code=400)
    with get_db() as conn:
        conn.execute(
            "UPDATE lexicon_entries SET canonical=?, variants=?, distinguish=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (data["canonical"], data["variants"], data["distinguish"], lid))
        updated = conn.execute("SELECT * FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if updated is None:
        return HTMLResponse("", status_code=404)
    invalidate_lexicon_caches()
    # 返回单行 partial（非整表）：与 lexicon_edit_row.html 保存按钮
    # hx-target="closest tr" hx-swap="outerHTML" 契约匹配，closest tr 被替换为合法新 <tr>；
    # confusable 行的区分说明列由 lexicon_row.html 按 kind=='confusable' 渲染。
    from app.main import templates
    return templates.TemplateResponse(request, "partials/lexicon_row.html", {
        "row": dict(updated), "kind": updated["kind"],
        "columns": KIND_COLUMNS.get(updated["kind"], KIND_COLUMNS["alias"])})


@router.delete("/lexicon/{lid}")
async def delete_lexicon(request: Request, lid: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM lexicon_entries WHERE id=?", (lid,))
    if cur.rowcount == 0:
        return HTMLResponse("", status_code=404)
    invalidate_lexicon_caches()
    return HTMLResponse("", headers={"HX-Trigger": "lexiconUpdated"})


@router.post("/lexicon/import")
async def import_lexicon(request: Request, file: UploadFile = File(...),
                         kind: str = Form("")):
    try:
        raw = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError:
        return HTMLResponse('<p style="color:red">❌ 文件编码不支持，请使用 UTF-8</p>',
                            status_code=400)
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
            # synonym/alias 组唯一（应用层）：同 (kind,canonical) 已有行 → 并入缺失
            # variants（UPDATE 补集合并，不改 is_active），否则 INSERT；confusable 仍全行幂等。
            if data["kind"] in EQUIV_KINDS:
                existing = _find_equiv_group(conn, data["kind"], data["canonical"])
                if existing:
                    cur_variants = [v for v in (existing["variants"] or "").split(",") if v.strip()]
                    incoming = [v for v in data["variants"].split(",") if v.strip()]
                    missing = [v for v in incoming if v not in cur_variants]
                    if missing:
                        conn.execute(
                            "UPDATE lexicon_entries SET variants=?, "
                            "updated_at=datetime('now','localtime') WHERE id=?",
                            (",".join(cur_variants + missing), existing["id"]))
                    ok += 1
                    continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note,updated_at)"
                " VALUES (?,?,?,?,?,datetime('now','localtime'))",
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
