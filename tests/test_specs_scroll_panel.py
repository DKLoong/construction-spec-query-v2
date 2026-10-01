"""规范列表滚动框（替代分页）：布局契约 + 全选语义。

背景：规范数增长后，全量列表会把「查看条文」/「分类」面板推到屏幕外，
需手动滚动。实测 50 本仅 1 077 个 DOM 节点 / 20 ms（远轻于已在用的
887 条条文框），故用限高滚动框而非分页——分页会引入跨页选择漏选问题。
"""
from pathlib import Path

TEMPLATE = "app/templates/partials/specs_table.html"
CSS = "static/app.css"


def test_spec_table_is_wrapped_in_scroll_container():
    """表格必须被 .spec-table-wrapper 包裹，否则长列表会推走下方面板。"""
    src = Path(TEMPLATE).read_text(encoding="utf-8")
    assert 'class="spec-table-wrapper"' in src, "缺少滚动容器包裹"


def test_scroll_container_height_fits_five_specs():
    """滚动框高度按「表头 + 5 行」定（实测 443px ≈ 27.8rem）。"""
    css = Path(CSS).read_text(encoding="utf-8")
    assert ".spec-table-wrapper" in css
    block = css.split(".spec-table-wrapper", 1)[1].split("}", 1)[0]
    assert "max-height" in block, "滚动框必须有 max-height"
    assert "27.8rem" in block, "高度应为容纳 5 本规范的 27.8rem"


def test_select_all_declares_whole_table_scope():
    """全选是全表语义，且必须显式告知——列表在滚动框内时「可见」≠「全部」。"""
    src = Path(TEMPLATE).read_text(encoding="utf-8")
    assert "勾选范围为全表" in src, "全选框需要 tooltip 说明作用范围"


def test_panel_areas_follow_the_spec_table_host():
    """两个面板区域必须排在列表宿主之后（限高后它们才落在一屏内）。

    ⚠ 这两个 div 定义在 `specs_list.html`，**不在** `specs_table.html`——
    后者是 `/specs/list` 返回的片段（只有表格），面板宿主在页面骨架里。
    """
    src = Path("app/templates/partials/specs_list.html").read_text(encoding="utf-8")
    host_at = src.index('id="specs-table"')
    assert src.index('id="spec-class-area"') > host_at
    assert src.index('id="clause-detail-area"') > host_at
