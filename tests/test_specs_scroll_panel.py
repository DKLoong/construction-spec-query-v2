"""规范列表滚动框（替代分页）：布局契约 + 全选语义。

背景：规范数增长后，全量列表会把「查看条文」/「分类」面板推到屏幕外，
需手动滚动。

**节点数口径（务必区分，2026-10-01 Task 9 在副本库 50 本上复测）：**
- **列表片段**（`/specs/list` 返回的表格，即页面里 `#specs-table` 宿主内）：1 072 个 DOM 节点
- **整页**（含页面骨架、导航、树面板、脚本等）：1 651 个 DOM 节点

初稿曾写「实测 50 本仅 1 077 个 DOM 节点 / 20 ms」——那是**片段口径**的节点数
（本次复测 1 072，与 1 077 差约 0.5%，属测量波动；当时数的是片段而非整页），
同句的 20 ms 也是**片段级渲染**的耗时，**不能与整页指标混用**（整页
`domContentLoadedEventEnd` 实测 172.7 ms）。两个口径都远轻于用户已在用且体感流畅的
887 条条文框（片段 11 972 个节点），故用限高滚动框而非分页——分页会引入跨页选择漏选问题。
"""
from pathlib import Path

TEMPLATE = "app/templates/partials/specs_table.html"
CSS = "static/app.css"


def test_spec_table_is_wrapped_in_scroll_container():
    """表格必须被 .spec-table-wrapper 包裹，否则长列表会推走下方面板。"""
    src = Path(TEMPLATE).read_text(encoding="utf-8")
    assert 'class="spec-table-wrapper"' in src, "缺少滚动容器包裹"


def test_scroll_container_height_fits_five_specs():
    """滚动框高度按「表头 + 5 行」定。

    实测（副本库 50 本，根字号 14.4px）：宽 ≥1280px 时表头 33.7px + 行高 39.2px，
    完整 5 行需 229.5px；取 17.5rem = 252px 可完整显示 5 行并露出第 6 行上半。
    行高随视口宽度浮动，固定高度无法在所有宽度下都恰好 5 行（1024px 下约 3 行）。
    """
    css = Path(CSS).read_text(encoding="utf-8")
    assert ".spec-table-wrapper" in css
    block = css.split(".spec-table-wrapper", 1)[1].split("}", 1)[0]
    assert "max-height" in block, "滚动框必须有 max-height"
    assert "17.5rem" in block, "高度应为容纳 5 本规范的 17.5rem"


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


def test_scroll_fires_before_request_not_after_load():
    """自动滚入视野必须挂在**请求发出前**，不得等加载完。

    挂 afterSettle 会让用户盯着没反应的界面等整本规范渲染完（实测 ~1.5 s，主线程
    被 4.1 MB 表格的同步布局堵住，见 TODOS T32）才看到滚动。面板排在列表之后，
    其顶部位置在内容插入前后不变，故提前滚过去的落点同样准确。
    """
    src = Path(TEMPLATE).read_text(encoding="utf-8")
    scroll_at = src.index("scrollIntoView")
    # 该 handler 必须注册在 beforeRequest 上
    assert "addEventListener('htmx:beforeRequest'" in src[:scroll_at], \
        "滚动应挂在 htmx:beforeRequest（请求发出前）"
    assert "addEventListener('htmx:afterSettle'" not in src, \
        "不得回退到 afterSettle——那要等内容加载完才滚"
    # 且必须是瞬时，不是平滑
    assert "scrollIntoView({ behavior: 'auto', block: 'start' })" in src, \
        "自动滚动应使用瞬时 behavior:'auto'"
    assert "behavior: 'smooth'" not in src, \
        "不得回退到 smooth——它会把「到位」再推迟上百毫秒"


def test_panel_buttons_declare_busy_state():
    """「查看条文」/「📋 分类」必须挂忙碌态：htmx 指示元素 + 禁用 + 对应样式。

    没有它，点击后到面板出现之间（实测最长约 2 s，主线程被 4.1 MB 表格的同步布局堵住）
    界面毫无反馈，用户会以为没点上。
    """
    src = Path(TEMPLATE).read_text(encoding="utf-8")
    # 两个按钮各一份（查看条文 / 分类），故至少出现两次
    assert src.count('class="outline spec-panel-btn"') == 2, \
        "两个面板按钮都应带 spec-panel-btn 类"
    assert src.count('hx-indicator="this"') == 2, "两个按钮都应指定 htmx 指示元素"
    assert src.count('hx-disabled-elt="this"') == 2, "两个按钮都应在请求期间禁用"

    css = Path(CSS).read_text(encoding="utf-8")
    assert ".spec-panel-btn.htmx-request" in css, "缺少忙碌态样式"
    assert "prefers-reduced-motion" in css.split(".spec-panel-btn", 1)[1], \
        "忙碌态转圈必须尊重减少动效偏好"
