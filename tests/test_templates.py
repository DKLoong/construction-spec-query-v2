"""模板结构回归测试：防止已修复的前端问题被回退

这些是静态断言，验证两个历史 bug 修复后的模板结构不再退化：
1. 导入表单「强制 OCR」复选框不得重新引入 width:auto
   （width:auto 使 Pico appearance:none checkbox 计算宽度仅 4px，
    导致 Chrome 合成器在取消选中时 :checked 背景图不重绘）
2. 设置弹窗 OCR 标签页必须保留「测试连通性」按钮与结果提示
   （OCR 多后端重构时曾丢失，按钮/JS/后端路由均存在但 HTML 缺失）
"""
from pathlib import Path

PARTIALS = Path(__file__).resolve().parent.parent / "app" / "templates" / "partials"


def _read(name: str) -> str:
    return (PARTIALS / name).read_text(encoding="utf-8")


def _read_static(name: str) -> str:
    """读取 static 目录下的前端文件（渲染逻辑已统一收敛到 md-render.js）"""
    return (PARTIALS.parent.parent.parent / "static" / name).read_text(encoding="utf-8")


def test_rules_numeric_fields_have_help_popover_and_range_error():
    """规则页 优先级/阈值 须有悬浮说明 + 失焦范围校验（对齐运维「参数设置」模块）

    参考实现 app/templates/partials/params_panel.html：focus 显示 .param-pop 说明、
    blur 校验并置 aria-invalid（Pico 原生渲染红边框）、越界显示 .param-err 红字。
    """
    html = _read("rules_list.html")
    for field in ("priority", "threshold"):
        for prefix in ("new", "edit"):
            key = f"{prefix}.{field}"
            assert f"focusField('{key}')" in html
            assert f"blurField('{key}')" in html
            assert f"invalid('{key}')" in html
            assert f"x-show=\"fieldErrs['{key}']\"" in html
            assert f"helpFor('{key}')" in html
    assert "您输入的参数超出可调范围，请重新输入" in html
    # Pico 靠 [aria-invalid=true] 出红边框，样式类需在位
    assert ".rule-pop" in html and ".rule-err" in html


def test_rules_subfield_label_carries_informational_hint():
    """子字段 label 后须有「信息性」提示语（该字段只用于沉淀规则时填 sub_field）"""
    html = _read("rules_list.html")
    assert html.count("（信息性，不参与匹配，可留空）") == 2  # 新建 + 编辑两处


def test_rules_subfield_dropdown_supports_keyboard_selection():
    """子字段下拉须支持 ↑↓ 选择 + Enter 填充 + Esc 关闭（原先只能鼠标点击）"""
    html = _read("rules_list.html")
    for target in ("new", "edit"):
        assert f"@keydown.arrow-down=\"onSubFieldArrow($event, 1)\"" in html
        assert f"@keydown.arrow-up=\"onSubFieldArrow($event, -1)\"" in html
        assert "@keydown.enter=\"onSubFieldEnter($event)\"" in html
        assert f"openSubField('{target}')" in html
        assert f"fetchSubFields('{target}')" in html
    assert "@keydown.escape=\"subFieldOpen = false\"" in html
    assert "sf-active" in html          # 键盘高亮项样式
    assert "pickSubField(s)" in html    # 鼠标与键盘共用同一填充出口
    assert "onSubFieldEnter(ev)" in html and "ev.preventDefault()" in html


def test_rules_page_has_view_toggle_and_single_load_path():
    """规则页须有「按规则 | 按标签」切换，且列表加载只走 JS（htmx 那条会绕过视图模式）"""
    html = _read("rules_list.html")
    assert "setView('rules')" in html and "setView('label')" in html
    assert "viewMode: 'rules'" in html
    # loadRules 按 viewMode 选端点
    assert "'/rules/grouped'" in html and "'/rules/list'" in html
    # 容器不得再挂 htmx 加载（否则恒取平铺列表，绕过分组视图）
    container = html.split('id="rules-table"', 1)[1].split(">", 1)[0]
    assert "hx-get" not in container, f"#rules-table 仍挂着 htmx 加载：{container}"
    # init 里注册 rulesListRefresh 监听（接管原 htmx from:body 触发点）
    assert "addEventListener('rulesListRefresh'" in html
    # 分组视图改标签会换组 → 编辑保存走整体重载
    assert "this.viewMode === 'label'" in html


def test_rules_view_toggle_sits_in_filter_row_right_aligned():
    """视图切换须与六维筛选同一行、靠右，且自身不换行

    实测教训：若视图键与筛选键同处一个 flex-wrap 行，宽度不足时会把「按标签」
    单独挤到第二行（截图确认）。故视图组须独立成列 + flex-shrink:0。
    """
    html = _read("rules_list.html")
    assert 'role="tablist"' not in html, "视图切换不应再是独立 tablist"
    row = html.split("<!-- 控制行", 1)[1].split('id="rules-table"', 1)[0]

    # 用完整 style 属性串定位（模板注释里也提到 flex-shrink:0 等词，短串会切错位置）
    left_attr = "display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;flex:1;min-width:0"
    right_attr = "display:flex;align-items:center;gap:0.5rem;flex-shrink:0"
    assert left_attr in row, "找不到可换行的筛选列"
    assert right_attr in row, "找不到不换行的视图列"

    left, right = row.split(right_attr, 1)
    assert "filterDim = ''" in left, "筛选键不在左列"
    assert "flex-wrap:wrap" in left, "筛选列自身不可换行，会连带把视图组挤走"
    assert "setView(" in right, "视图键不在右列"
    assert "filterDim" not in right, "右列混进了筛选键"
    # 两个视图键必须同处右列 → 永远不会被拆到两行
    assert "setView('rules')" in right and "setView('label')" in right


def test_rules_view_toggle_buttons_match_filter_chips_for_equal_height():
    """视图键须与筛选键同构（都 outline + contrast 选中态、都不覆盖字号）→ 等高、文字同基线"""
    html = _read("rules_list.html")
    row = html.split("<!-- 控制行", 1)[1].split('id="rules-table"', 1)[0]
    for mode in ("rules", "label"):
        btn = row.split(f"setView('{mode}')", 1)[0].rsplit("<button", 1)[1]
        assert 'class="outline"' in btn, f"{mode} 键丢了 outline 基类"
        assert f":class=\"{{ contrast: viewMode === '{mode}' }}\"" in btn, \
            f"{mode} 键未用与筛选键一致的 contrast 选中态"
        assert "font-size" not in btn, f"{mode} 键覆盖了字号 → 与筛选键不等高"


def test_rules_action_buttons_clear_pico_phantom_margin():
    """操作列按钮须清零 Pico 的默认 margin-bottom，否则撑高单元格、与相邻列不垂直居中"""
    html = _read("rules_list.html")
    assert ".row-actions { display:flex; gap:0.25rem; align-items:center; }" in html
    assert ".row-actions button { margin:0; }" in html
    assert "#rules-table td { vertical-align:middle; }" in html
    # 规则页三个片段都改用共用类（分组 + 平铺表 + 单行片段）
    for name in ("rules_grouped.html", "rules_table.html", "rules_row.html"):
        assert 'class="row-actions"' in _read(name), f"{name} 未使用 .row-actions"
        assert 'display:flex;gap:0.25rem' not in _read(name), f"{name} 仍留着旧内联写法"


def test_rules_grouped_header_is_dark_band_with_light_text():
    """分组头须为深底 + 近白字（浅灰底+灰字仅 2.74:1；只改白字会掉到 1.29:1）"""
    html = _read("rules_list.html")
    head = html.split(".rule-group-head {", 1)[1].split("}", 1)[0]
    assert "background:var(--pico-contrast-background)" in head, "组头未改深底"
    assert "color:var(--pico-primary-inverse)" in head, "组头文字未改近白"
    meta = html.split(".rule-group-head .meta {", 1)[1].split("}", 1)[0]
    assert "var(--pico-primary-inverse)" in meta, "组头小字仍是灰字"
    assert "var(--pico-muted-color)" not in meta, "组头小字残留灰字"


def test_rules_grouped_add_keyword_button_is_filled_reference_style():
    """「+ 添加关键词」须参考 AI 分类键：纯色填充 + 白字（不再是描边按钮）"""
    html = _read("rules_grouped.html")
    btn = html.split("+ 添加关键词", 1)[0].rsplit("<button", 1)[1]
    assert 'class="secondary"' in btn
    assert "background:var(--pico-primary-background)" in btn
    assert "color:white" in btn
    assert 'class="outline"' not in btn


def test_rules_grouped_partial_has_no_inline_style_block():
    """分组片段不得内置样式元素：它经 innerHTML 反复装载，内联样式会不断累积"""
    import re
    html = _read("rules_grouped.html")
    assert not re.search(r"^\s*<style>", html, re.M), "分组片段仍含内联 <style> 元素"


def test_rules_grouped_rows_use_js_handlers_not_row_patch():
    """分组视图的行内操作须走 JS 处理器（操作后整体重载），不复用扁平行片段交换"""
    html = _read("rules_grouped.html")
    assert "toggleRule({{ r.id }})" in html
    assert "lockRule({{ r.id }})" in html
    assert "deleteRule({{ r.id }}, '{{ r.pattern }}')" in html
    assert "openCreateFor('{{ g.dimension }}', '{{ g.label }}')" in html
    # 不得出现平铺片段那种 htmx 局部交换（分组视图无对应 <tr> 可换）
    assert "hx-target" not in html
    assert "hx-post" not in html and "hx-delete" not in html


def test_rules_form_omits_unusable_exact_match_type():
    """匹配方式不得再提供 exact：它要求 pattern 逐字等于「正文+父路径」全串，
    实战永不命中（库内 113/113 规则均为 keyword，从未被使用）——留着即是静默死规则"""
    html = _read("rules_list.html")
    assert 'value="exact"' not in html
    # 仍保留另两种
    assert html.count('value="keyword"') == 2   # 新建 + 编辑
    assert html.count('value="regex"') == 2


def test_rules_pattern_field_carries_writing_hint():
    """关键词须给写法提示：用规范术语（写俗称永不命中）+ 避免过泛短词（子串计数会虚高）"""
    html = _read("rules_list.html")
    assert html.count("（用规范术语；避免过泛短词）") == 2   # 新建 + 编辑
    for prefix in ("new", "edit"):
        assert f"helpFor('{prefix}.pattern')" in html
        assert f"focusField('{prefix}.pattern')" in html
        assert f"blurField('{prefix}.pattern')" in html
    assert "pattern: " in html          # helpText 里有 pattern 的说明文案
    assert "俗称" in html and "规范" in html


def test_rules_validate_field_tolerates_non_numeric_field():
    """validateField 对无范围定义的字段（如 pattern）必须直接放行，不得读 undefined.min"""
    html = _read("rules_list.html")
    assert "if (!range) { this.fieldErrs[k] = false; return; }" in html


def test_force_ocr_checkbox_uses_pico_default_width():
    """强制OCR复选框不得带 width:auto（否则宽度塌缩为4px导致视觉不刷新）"""
    html = _read("tree_panel.html")
    line = next(l for l in html.splitlines() if 'name="force_ocr"' in l)
    assert "width:auto" not in line, "force_ocr 复选框不应使用 width:auto"
    assert 'type="checkbox"' in line
    assert 'value="true"' in line


def test_settings_ocr_tab_has_test_connection_button():
    """OCR 标签页必须含测试连通性按钮与结果提示（在 AI 标签页之前）"""
    html = _read("settings_dialog.html")
    ocr_part = html.split("<!-- AI 标签页 -->")[0]
    assert "testOCR()" in ocr_part, "OCR 标签页缺少 testOCR() 调用"
    assert "ocrTestResult" in ocr_part, "OCR 标签页缺少 ocrTestResult 结果绑定"
    assert "currentOCRToken" in ocr_part


def test_ocr_review_rewrite_keeps_imgs_segment():
    """审查页图片路径改写必须保留 imgs/ 子路径段

    曾误把 `src="imgs/xxx.jpg"` 的 imgs/ 前缀整个替换掉，导致请求
    `/import/review/{task_id}/img_in_*.jpg`（缺 imgs/ 段）→ 路由 404 → 破图图标。
    改写逻辑已收敛到 md-render.js 的 rewriteImg，审查页走统一 mdRender 渲染。
    """
    html = _read("ocr_review.html")
    md_render = _read_static("components/md-render.js")
    assert "mdRender.renderHtml" in html, "审查页预览应走统一 mdRender 渲染"
    # HTML <img src="imgs/..."> 改写时，base 后必须补回 imgs/，不能把 imgs/ 段替换掉
    assert "'$1' + baseUrl + 'imgs/'" in md_render, "HTML 图片改写应保留 imgs/ 子路径"
    # markdown 语法 ![..](imgs/..) 同样保留 imgs/
    assert "'![$1](' + baseUrl + 'imgs/'" in md_render, "markdown 图片改写应保留 imgs/ 子路径"


def test_clause_class_edit_cancel_restores_single_row():
    """分类编辑取消按钮应只恢复当前行（row-class），而非整列表重渲染"""
    html = _read("clause_class_edit.html")
    assert "row-class" in html, "取消应指向单行恢复路由"
    assert "clause-detail-area" not in html, "取消不应重渲染整列表"


def test_clause_edit_page_two_column_layout():
    """编辑页须为两栏布局（左编辑右实时预览），含右下角确认/取消按钮"""
    html = _read("clause_edit_page.html")
    assert "review-panels" in html, "编辑页应复用双栏布局"
    assert "review-editor" in html, "左侧应为编辑区"
    assert "review-preview" in html, "右侧应为预览区"
    assert "mdRender.renderHtml" in html, "预览应走统一 mdRender 渲染"
    assert "确认" in html, "应有确认按钮"
    assert "取消" in html, "应有取消按钮"


def test_clause_edit_page_embeds_data_as_js_literal():
    """编辑页数据应以 JS 字面量嵌入（tojson 在 script 内由 JS 引擎解码），避免 dataset 乱码"""
    html = _read("clause_edit_page.html")
    assert "__clauseEditData" in html, "应通过 JS 字面量嵌入条文数据"
    assert "data-content=" not in html, "不应再通过 data-content 属性嵌入（tojson 的 \\uXXXX 经 dataset 直读不乱码）"


def test_clause_edit_page_has_sync_scroll():
    """编辑页编辑框与预览框应同步滚动"""
    html = _read("clause_edit_page.html")
    assert "syncScroll" in html, "应有同步滚动方法"
    assert "@scroll" in html, "编辑框应绑定 @scroll 同步滚动"


def test_specs_list_return_reloads_clauses():
    """编辑页返回后（取消或保存）都应重新加载条文列表以恢复查看上下文"""
    html = _read("specs_list.html")
    assert "htmx.ajax('GET', '/specs/' + data.specId + '/clauses'" in html, "返回后应重载条文列表"
    assert "clause-detail-area" in html


def test_clauses_table_edit_button_navigates_to_edit_page():
    """条文列表「编辑」按钮应跳转独立编辑页（editClause 记录滚动位置）"""
    html = _read("clauses_table.html")
    specs = _read("specs_list.html")
    assert "editClause" in html, "编辑按钮应调用 editClause 跳转"
    assert "edit-page" in specs, "editClause 应跳转独立编辑页"
    assert "clauseEditReturn" in specs, "跳转前应记录返回滚动位置（sessionStorage）"


def test_clause_detail_renders_markdown_with_sanitize():
    """条文详情必须用 tojson 传 content，经统一渲染（marked+DOMPurify+KaTeX，含图片改写）"""
    html = _read("clause_detail.html")
    md_render = _read_static("components/md-render.js")
    assert "tojson" in html, "content 应以 tojson 安全传递"
    assert "mdRender.renderInto" in html, "条文详情应走统一 mdRender 渲染"
    # 净化与渲染逻辑统一在 md-render.js：marked → DOMPurify → KaTeX
    assert "DOMPurify.sanitize" in md_render, "渲染必须经 DOMPurify 净化（防 XSS）"
    assert "marked.parse" in md_render, "需用 marked 渲染 markdown"
    assert "katexize" in md_render, "需用 KaTeX 渲染公式"
    # 图片相对路径改写为 /specs/{specId}/imgs/
    assert "specs/${specId}/" in html, "条文图片相对引用应改写为 spec 图片路由"


def test_katex_strict_ignores_unicode_text_in_math_mode():
    """数学模式中文（如 $重=50kg$）不得刷控制台警告

    KaTeX strict 默认 warn，会对数学模式中的中文触发 unicodeTextInMathMode
    警告（渲染本身正常，经 cjk_fallback 显示）。katexize 须显式只忽略该类，
    其余 strict 检查（如 href 注入等）保持默认 warn，避免掩盖潜在问题。
    """
    md_render = _read_static("components/md-render.js")
    assert "unicodeTextInMathMode" in md_render, "需显式处理 unicodeTextInMathMode 警告"
    assert "'ignore'" in md_render, "中文警告应置为 ignore 以消除控制台噪音"


def test_clauses_table_preview_renders_markdown():
    """条文列表预览列须渲染 markdown（clause-preview-md 容器 + 统一 mdRender 渲染）"""
    html = _read("clauses_table.html")
    assert "clause-preview-md" in html, "预览列缺少渲染容器"
    assert "tojson" in html, "预览列 content 应以 tojson 传递"
    # 渲染统一收敛到 md-render.js（含 DOMPurify 净化 + 图片改写），避免两处逻辑漂移
    assert "mdRender.renderInto" in html, "预览列应复用统一 mdRender 渲染"


def test_search_state_store_registered_in_tree_js():
    """分类树组件应注册全局 searchState store（搜索框/分类树/问答共享筛选状态）"""
    js = _read_static("components/tree.js")
    assert "searchState" in js, "tree.js 应定义 searchState store"
    assert "$store" in js, "treeView 应通过 $store 读写共享状态"


def test_search_box_reads_store_filters():
    """搜索框应读取 store 中的分类筛选参数，避免丢失筛选状态"""
    js = _read_static("components/search.js")
    assert "searchState" in js, "search.js 应引用 searchState store"
    assert "$store" in js, "searchBox 应通过 $store 读取共享筛选状态"


def test_qa_modal_sends_current_filters():
    """AI 问答提交时应携带当前分类筛选（联动收窄检索范围）"""
    js = _read_static("components/qa.js")
    assert "searchState" in js, "qa.js 应引用 searchState store"
    assert "$store" in js, "qaView 应通过 $store 读取共享筛选状态"


def test_edit_clause_saves_table_scroll():
    """编辑跳转前应同时记录条文表格滚动容器（.clause-table-wrapper）位置，返回时一并恢复"""
    html = _read("specs_list.html")
    assert "clause-table-wrapper" in html, "应保存条文表格滚动容器位置"
    assert "tableScroll" in html, "应记录 tableScroll"
    assert "data.tableScroll" in html, "返回时应恢复 tableScroll"


def test_clause_edit_preview_updates_on_title_clause_no():
    """修改条文号/标题应触发预览刷新（与 content 一致，三个输入都绑 @input）"""
    html = _read("clause_edit_page.html")
    assert html.count('@input="updatePreview()"') >= 3, "条文号/标题/内容输入都应在输入时刷新预览"


def test_return_reload_defers_htmx():
    """保存返回后重载条文列表应延迟执行——bfcache 恢复早期立即 htmx.ajax 会静默失效"""
    html = _read("specs_list.html")
    assert "setTimeout" in html, "重载应延迟执行"
    assert "htmx.ajax" in html, "仍应通过 htmx.ajax 重载"


def test_import_autofill_tracks_auto_filled_state():
    """自动填充应跟踪来源：选错文件后再次选择可覆盖自动填充值，但保留用户手动输入"""
    js = _read_static("components/import.js")
    assert "autoFilled" in js, "应跟踪自动填充状态"
    # 不再整体跳过（旧逻辑：任一输入框有值就整体跳过，导致选错文件后无法重新识别）
    assert "codeInput.value.trim() || titleInput.value.trim()" not in js, "不应因任一输入框有值而整体跳过"
    assert "this.autoFilled.code" in js, "应按字段判断是否更新（为空或来自自动填充）"
    assert "this.autoFilled.title" in js


def test_import_inputs_clear_autofill_on_manual_edit():
    """用户手动编辑输入框后应清除自动填充标记，后续选择文件不覆盖手动输入值"""
    html = _read("tree_panel.html")
    assert "markManual" in html, "手动编辑应调用 markManual 清除自动填充标记"
    assert 'name="code"' in html
    assert 'name="title"' in html


def test_search_dispatch_no_after_settle_listener():
    """搜索函数不应累积 htmx:afterSettle 监听器（滚动由结果页 hx-on 处理）。

    允许 search.js **模块级一次性**注册（翻页建议/隐藏雷达用），但禁止在
    search()/dispatchSearch 函数内动态累积（那会随每次搜索越加越多）。
    """
    tree_js = _read_static("components/tree.js")
    search_js = _read_static("components/search.js")
    assert "addEventListener('htmx:afterSettle'" not in tree_js, "tree.js 不应注册滚动监听器"
    assert search_js.count("addEventListener('htmx:afterSettle'") <= 1, \
        "search.js 仅允许模块级一次性注册，禁止动态累积"


def test_search_dispatch_sends_all_flag():
    """主动搜索/筛选但无关键词无筛选时，应发 all=1 显示全部条文而非空提示"""
    tree_js = _read_static("components/tree.js")
    search_js = _read_static("components/search.js")
    assert "'all', '1'" in tree_js, "分类树取消所有筛选应带 all=1"
    assert "'all', '1'" in search_js, "搜索框应带 all=1"


def test_md_render_converts_literal_newline_to_br():
    """md-render 必须把 Paddle 输出的字面 \\n（反斜杠+n 两字符）替换为 <br>

    PaddleOCR 在 HTML 表格单元格内用字面 \\n 表示换行；若替换为真实换行，
    浏览器对 HTML 单元格内的空白会折叠成空格，仍不换行，故必须替换为 <br>。
    """
    md_render = _read_static("components/md-render.js")
    # JS 正则字面量 /\\n/g（两个反斜杠 + n）用于匹配「字面反斜杠+n」两字符；
    # Python 源码中需写 "\\\\n" 才能表示两个反斜杠字符。
    assert "/\\\\n/g" in md_render, "md-render 应含字面 \\n 匹配正则"
    assert "'<br>'" in md_render, "字面 \\n 应替换为 <br> 而非真实换行"


def test_search_include_non_clause_checkbox_present():
    """检索页须有「包含前言·条文说明」复选框（勾选即重搜）"""
    html = _read("tree_panel.html")
    assert "include_non_clause" in html, "tree_panel 应含 include_non_clause 复选框"
    assert 'type="checkbox"' in html
    assert "包含前言·条文说明" in html, "复选框应有说明文案"


def test_search_include_non_clause_carried_by_search_js():
    """searchBox 应持有 includeNonClause 状态并在发起搜索时携带 include_non_clause=1"""
    js = _read_static("components/search.js")
    assert "includeNonClause" in js, "searchBox 应含 includeNonClause 状态"
    assert "include_non_clause" in js, "search.js 发起搜索时应携带 include_non_clause"


def test_search_include_non_clause_carried_by_tree_js():
    """searchState store 应含 includeNonClause，分类树触发搜索时同样携带该参数"""
    js = _read_static("components/tree.js")
    assert "includeNonClause" in js, "searchState store 应含 includeNonClause"
    assert "include_non_clause" in js, "tree.js dispatchSearch 应携带 include_non_clause"


def test_search_include_non_clause_checkbox_styled():
    """复选框样式：文字单行(nowrap)、复选框正方形(等宽高)、文字行高与复选框一致对齐"""
    import re
    html = _read("tree_panel.html")
    # 复选框 input：显式等宽高正方形，不被 flex 拉伸（标签属性可能跨行，正则匹配完整标签）
    m = re.search(r'<input[^>]*x-model="includeNonClause"[^>]*>', html, re.S)
    assert m, "应找到 include_non_clause 复选框 input"
    input_tag = m.group(0)
    assert "width:0.875rem" in input_tag, "复选框应固定宽度"
    assert "height:0.875rem" in input_tag, "复选框应为正方形（宽高相等）"
    assert "flex:none" in input_tag, "复选框不应被 flex 拉伸/压缩"
    # 标签：单行不换行
    label_line = next(l for l in html.splitlines() if "包含前言·条文说明" in l)
    assert "white-space:nowrap" in label_line, "文字应单行不换行"


def test_search_ce_rerank_checkbox_present():
    """左栏应有「启用 CE 精排」复选框（searchBox 状态）"""
    html = _read("tree_panel.html")
    assert "ceRerank" in html, "tree_panel 应含 ceRerank 复选框绑定"
    assert "启用 CE 精排" in html, "复选框应有说明文案"


def test_search_ce_rerank_carried_by_js():
    """searchBox 与 dispatchSearch 应携带 ce_rerank 参数（热切换）"""
    js = _read_static("components/search.js")
    tree_js = _read_static("components/tree.js")
    assert "ceRerank" in js, "searchBox 应含 ceRerank 状态"
    assert "ce_rerank" in js, "search.js 发起搜索时应携带 ce_rerank"
    assert "ceRerank" in tree_js, "searchState store 应含 ceRerank"
    assert "ce_rerank" in tree_js, "tree.js dispatchSearch 应携带 ce_rerank"


def test_search_toast_and_radar_present():
    """前端应有轻提示文案与雷达动画（CE 等待缓解）"""
    js = _read_static("components/search.js")
    assert "CE精排已开启" in js, "勾选 CE 应有轻提示文案"
    assert "对搜索结果不满意" in js, "翻页建议应有轻提示文案"
    assert "radar" in js.lower(), "应有雷达动画控制"
    # 热切换：勾选/取消立即重搜
    assert "onCeChange" in js, "CE 复选框变化应触发 onCeChange（弹提示 + 重搜）"
    # 雷达仅搜索请求触发（排除条文详情弹窗等其它 htmx 请求）
    assert "path.startsWith('/search')" in js, "雷达应仅对 /search 请求触发"
    # 翻页过半建议展示 4s（阅读体验）
    assert "4000" in js, "翻页建议轻提示应展示 4 秒"
    # 轻提示挂 body（不随 .center-panel-v2 htmx 刷新被冲掉）
    assert "document.body.appendChild" in js, "轻提示应挂到 body，不随搜索刷新消失"
    # 翻页建议：过半 + 最多 3 页兜底（min）+ 每会话仅提示一次
    assert "_SUGGEST_PAGE_CAP" in js, "应有翻页建议页数上限常量（兜底提前）"
    assert "_suggestShown" in js, "应有每会话仅提示一次的标记（避免反复弹）"


def test_result_list_renders_pagination_meta():
    """结果列表应渲染页码元数据（翻页建议判断用 data-total-pages）"""
    html = _read("result_list.html")
    assert "data-total-pages" in html, "结果容器应渲染总页数"
    assert "data-page" in html, "结果容器应渲染当前页"
