"""词库管理路由：/lexicon 三 Tab（synonym/alias/confusable）统一 CRUD + CSV 导入。"""
import csv
import io

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, Response
from app.database import get_db
from app.lexicon.store import invalidate_lexicon_caches
from app.lexicon.validation import validate_row
from app.lexicon.store import EQUIV_KINDS, find_equiv_conflict
from app.logging_util import log_action, json_detail

router = APIRouter()

# 跨组词面冲突的文案（create/edit/import 三路径共用，避免措辞漂移）
_CONFLICT_MSG = "词「{}」已属于其它等价组，请先在该组补充/调整，避免同一词面被两组占用"


def _equiv_words(data: dict | None) -> list[str]:
    """validate_row 输出 → 占用词面列表（canonical + variants）；None/空 → 空列表"""
    if not data:
        return []
    return [data["canonical"], *[v.strip() for v in (data["variants"] or "").split(",") if v.strip()]]


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

# ── CSV 表头中英兼容（导入与模板共用）────────────────────────────
# 列名归一：任一别名命中即取该列；类型列额外支持中文值 → 英文 kind。
_CSV_HEADER_ALIASES = {
    "kind": ("kind", "类型"),
    "canonical": ("canonical", "主词", "词条", "代表词", "规范词", "术语", "词A"),
    "variants": ("variants", "关联词", "变体", "变体词", "俗称", "等价词", "词B"),
    "distinguish": ("distinguish", "区分说明"),
    "note": ("note", "备注"),
}
_CSV_KIND_ZH = {"同义词": "synonym", "别名": "alias", "易混淆术语": "confusable"}


def _csv_val(row: dict, key: str) -> str:
    """按中英别名取 CSV 行某列值（首个非空别名命中）。"""
    for alias in _CSV_HEADER_ALIASES[key]:
        v = row.get(alias)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _csv_kind(row: dict, tab_kind: str) -> str:
    """CSV 类型列取值：中文映射为英文 kind；空则回退当前 Tab kind（英文）。"""
    raw = _csv_val(row, "kind")
    if not raw:
        return tab_kind
    return _CSV_KIND_ZH.get(raw, raw)

# 词库 CSV 导入模板（few-shot：每类给示例行；UTF-8 BOM 供 Excel 正确识别中文）。
# 表头中文，语义由「类型」列决定：主词/关联词 在 同义词=代表词/等价词，
# 别名=规范词/俗称，易混淆=词A/词B。字段内含中文逗号安全，勿用 ASCII 逗号分隔。
LEXICON_TEMPLATE_CSV = (
    "类型,主词,关联词,区分说明,备注\r\n"
    "同义词,坍落度,塌落度,,同一试验的两种写法视为同义；二者可互相替换\r\n"
    "别名,混凝土,砼,,工地俗称映射到规范词；检索/规则会把它归一到规范词\r\n"
    "易混淆术语,圈梁,构造柱,"
    "圈梁为沿墙高横向布置的水平约束构件；构造柱为墙端与交角处的竖向约束构件；二者同属抗震构造措施"
    ",易混淆必须写明区分说明；不会做检索改写\r\n"
)
LEXICON_TEMPLATE_CSV = "﻿" + LEXICON_TEMPLATE_CSV  # BOM：Excel 以 UTF-8 打开不乱码


@router.get("/lexicon")
async def lexicon_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "lexicon.html",
    })


@router.get("/lexicon/template.csv")
async def lexicon_template_download():
    """下载词库 CSV 导入模板（few-shot 示例，供 WebUI「下载模板」键）"""
    return Response(
        content=LEXICON_TEMPLATE_CSV,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="lexicon_template.csv"'})


@router.get("/lexicon/list")
async def lexicon_list(request: Request, kind: str = "synonym", active: str = ""):
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
    # validate_row 契约：err 为空 ⟹ data 必为 dict。
    # 该契约由 tests/test_lexicon_validation.py 逐条错误路径守护；此处断言让类型检查器
    # 也认可（Pyright 不做「元组两元素互斥」的跨元素窄化），且违约时立刻暴露而非静默 500。
    assert data is not None
    with get_db() as conn:
        # synonym/alias 组唯一（应用层）：同 kind canonical 已存在 → 拒绝并立。
        # 用 validate_row 返回的 data["kind"]（已 strip），不用原始 Form kind。
        if data["kind"] in EQUIV_KINDS:
            if _find_equiv_group(conn, data["kind"], data["canonical"]):
                return HTMLResponse(
                    '<p style="color:orange">⚠️ 该代表词已有词条，请在其变体列补充</p>', status_code=400)
            conflict = find_equiv_conflict(conn, _equiv_words(data))
            if conflict:
                return HTMLResponse(
                    f'<p style="color:red">❌ {_CONFLICT_MSG.format(conflict)}</p>', status_code=400)
        cur = conn.execute(
            "INSERT OR IGNORE INTO lexicon_entries(kind,canonical,variants,distinguish,note,updated_at)"
            " VALUES (?,?,?,?,'WebUI 新增',datetime('now','localtime'))",
            (data["kind"], data["canonical"], data["variants"], data["distinguish"]))
    invalidate_lexicon_caches()
    if cur.rowcount == 0:
        return HTMLResponse('<p style="color:orange">⚠️ 该词条已存在（全行幂等）</p>',
                            headers={"HX-Trigger": "lexiconUpdated"})
    log_action("lexicon", "INFO", "新增词条",
               detail=json_detail({"kind": data["kind"], "canonical": data["canonical"],
                                   "variants": data["variants"]}),
               username=getattr(request.state, "username", ""))
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
    log_action("lexicon", "INFO", "启停词条",
               detail=json_detail({"id": lid, "canonical": row["canonical"],
                                   "is_active": row["is_active"]}),
               username=getattr(request.state, "username", ""))
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
    assert data is not None  # 同 create：validate_row 契约，见 test_lexicon_validation.py
    if row["kind"] in EQUIV_KINDS:
        with get_db() as conn:
            dup = _find_equiv_group(conn, row["kind"], data["canonical"])
            if dup and dup["id"] != lid:
                return HTMLResponse('<p style="color:orange">⚠️ 已有同代表词词条</p>', status_code=400)
            # exclude_id=lid：本行自己的词面不算冲突
            conflict = find_equiv_conflict(conn, _equiv_words(data), exclude_id=lid)
            if conflict:
                return HTMLResponse(
                    f'<p style="color:red">❌ {_CONFLICT_MSG.format(conflict)}</p>', status_code=400)
    with get_db() as conn:
        conn.execute(
            "UPDATE lexicon_entries SET canonical=?, variants=?, distinguish=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (data["canonical"], data["variants"], data["distinguish"], lid))
        updated = conn.execute("SELECT * FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
    if updated is None:
        return HTMLResponse("", status_code=404)
    invalidate_lexicon_caches()
    log_action("lexicon", "INFO", "编辑词条",
               detail=json_detail({"id": lid, "canonical": data["canonical"],
                                   "variants": data["variants"],
                                   "distinguish": data["distinguish"]}),
               username=getattr(request.state, "username", ""))
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
        # 先取旧行，供审计记录「删掉的是什么」（删除后无法追溯）
        old = conn.execute(
            "SELECT kind, canonical, variants FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
        cur = conn.execute("DELETE FROM lexicon_entries WHERE id=?", (lid,))
    if cur.rowcount == 0:
        return HTMLResponse("", status_code=404)
    invalidate_lexicon_caches()
    log_action("lexicon", "INFO", "删除词条",
               detail=json_detail({"id": lid, "kind": old["kind"],
                                   "canonical": old["canonical"],
                                   "variants": old["variants"]}),
               username=getattr(request.state, "username", ""))
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
            k = _csv_kind(rec, kind)  # 类型列支持中文/英文/空(按当前 Tab)
            data, err = validate_row(k, _csv_val(rec, "canonical"), _csv_val(rec, "variants"),
                                     _csv_val(rec, "distinguish"))
            if err:
                fail += 1
                fails.append(f"第{line_no}行: {err}")
                continue
            assert data is not None  # 同上：validate_row 契约
            key = (data["kind"], data["canonical"], data["variants"])
            if key in seen:
                skip += 1
                continue
            seen.add(key)
            # synonym/alias 组唯一（应用层）：同 (kind,canonical) 已有行 → 并入缺失
            # variants（UPDATE 补集合并，不改 is_active），否则 INSERT；confusable 仍全行幂等。
            if data["kind"] in EQUIV_KINDS:
                existing = _find_equiv_group(conn, data["kind"], data["canonical"])
                # 跨组词面校验：与同 (kind,canonical) 的合并目标互相排除，其余组一律拦住
                conflict = find_equiv_conflict(
                    conn, _equiv_words(data),
                    exclude_id=existing["id"] if existing else None)
                if conflict:
                    fail += 1
                    fails.append(f"第{line_no}行: {_CONFLICT_MSG.format(conflict)}")
                    continue
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
                 _csv_val(rec, "note") or "CSV 导入"))
            if cur.rowcount == 0:
                skip += 1
            else:
                ok += 1
    invalidate_lexicon_caches()
    log_action("lexicon", "INFO", "CSV导入词库",
               detail=json_detail({"ok": ok, "skip": skip, "fail": fail,
                                   "kind": kind or "auto"}),
               username=getattr(request.state, "username", ""))
    from app.main import templates
    # 触发列表刷新：与增删启禁一致，导入也可能新增/合并词条，需重拉当前 kind 列表
    return templates.TemplateResponse(request, "partials/lexicon_import_result.html",
                                      {"ok": ok, "skip": skip, "fail": fail, "fails": fails},
                                      headers={"HX-Trigger": "lexiconUpdated"})
