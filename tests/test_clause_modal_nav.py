"""条文详情弹窗翻页（T1）：模板侧数据契约 + 弹窗结构回归

**分工**：弹窗内的真实交互（点「下一条」内容是否切换、「上一条」在首条是否禁用、
计数是否等于 当前/总数）由 UI 探针覆盖（`scripts/probe_spec_ui.py`）；
本文件锁定模板侧的**数据契约**——这些属性/节点缺失时前端根本拿不到翻页上下文，
而且这种缺失不会在任何后端测试里暴露。

**id 序列的存法（有意为之）**：全量 id 只存一份（容器上的 `data-clause-ids`），
每个可点元素只带自己的 `data-clause-id`，序号由前端按 id 反查。
禁止改成「每个元素各存一份全量列表」——那是 O(N²)，实测 73 条条文即 30KB
（正确存法合计 3.4KB，而同页 `data-md` 本就 106KB）。
"""
import json
import re
from pathlib import Path

from app.database import get_db, init_db
from tests.conftest import setup_search_data
from tests.test_spec_routes import _setup_spec_data

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
BASE_HTML = (TEMPLATES / "base.html").read_text(encoding="utf-8")

CLAUSE_ID_ATTR = re.compile(r'data-clause-id="(\d+)"')
CLAUSE_IDS_ATTR = re.compile(r"data-clause-ids='([^']*)'")


# ═══════════════════════════════════════════
# 规范管理页：条文列表
# ═══════════════════════════════════════════

def test_clauses_table_exposes_clause_ids(auth_client, monkeypatch, tmp_path):
    """条文表格容器须暴露全量 id 序列（且只有一份）"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "cmn_ids.db"))
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? ORDER BY id", (spec_id,))]

    resp = auth_client.get(f"/specs/{spec_id}/clauses")
    assert resp.status_code == 200

    matches = CLAUSE_IDS_ATTR.findall(resp.text)
    assert len(matches) == 1, f"data-clause-ids 应恰好出现一次，实际 {len(matches)} 次"
    assert json.loads(matches[0]) == ids


def test_clauses_table_preview_column_is_clickable(auth_client, monkeypatch, tmp_path):
    """每个内容列元素须带自己的 clause id（翻页序号由前端按 id 反查）"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "cmn_click.db"))
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        ids = [str(r["id"]) for r in conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? ORDER BY id", (spec_id,))]

    resp = auth_client.get(f"/specs/{spec_id}/clauses")
    assert CLAUSE_ID_ATTR.findall(resp.text) == ids


# ═══════════════════════════════════════════
# 检索结果页
# ═══════════════════════════════════════════

def test_search_results_expose_clause_ids(auth_client, monkeypatch, tmp_path):
    """检索结果容器暴露本页 id 序列，每个结果项带自己的 id"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "cmn_search.db"))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=钢筋")
    assert resp.status_code == 200

    matches = CLAUSE_IDS_ATTR.findall(resp.text)
    assert len(matches) == 1, f"data-clause-ids 应恰好出现一次，实际 {len(matches)} 次"
    ids = json.loads(matches[0])
    assert len(ids) == 1, "关键词「钢筋」应只命中 setup_search_data 的第 2 条"
    assert CLAUSE_ID_ATTR.findall(resp.text) == [str(i) for i in ids]


# ═══════════════════════════════════════════
# 弹窗底部翻页控件
# ═══════════════════════════════════════════

def test_clause_modal_footer_exists_inside_body():
    """翻页条须存在于弹窗盒内、且在正文之后"""
    assert 'id="clause-modal-footer"' in BASE_HTML, "弹窗缺少底部翻页条容器"
    assert BASE_HTML.index('id="clause-modal-body"') < BASE_HTML.index('id="clause-modal-footer"')


def test_clause_modal_footer_control_order():
    """控件从左到右须为「上一条 / 条数统计 / 下一条」"""
    i_prev = BASE_HTML.index('id="clause-nav-prev"')
    i_count = BASE_HTML.index('id="clause-nav-count"')
    i_next = BASE_HTML.index('id="clause-nav-next"')
    assert i_prev < i_count < i_next, "翻页控件顺序须为 上一条 → 计数 → 下一条"


def test_clause_modal_footer_hidden_by_default():
    """默认隐藏：无翻页上下文（如从历史入口单独打开）时不得出现空白条"""
    m = re.search(r'id="clause-modal-footer"[^>]*style="([^"]*)"', BASE_HTML)
    assert m, "翻页条缺少 style 属性"
    assert "display:none" in m.group(1).replace(" ", "")


def test_clause_modal_nav_buttons_have_explicit_type():
    """翻页按钮须显式 type="button"

    Pico 对**有显式 type 属性**的 button 应用不同的 margin 规则，同级混用时
    会造成 7.2px 的高度差（见 memory: pico-button-margin-bottom-flex）；
    三个控件在同一 flex 行内基线对齐，必须同款。
    """
    for btn_id in ("clause-nav-prev", "clause-nav-next"):
        m = re.search(rf"<button[^>]*id=\"{btn_id}\"[^>]*>", BASE_HTML)
        assert m, f"未找到按钮 {btn_id}"
        assert 'type="button"' in m.group(0), f"{btn_id} 缺显式 type=\"button\""
