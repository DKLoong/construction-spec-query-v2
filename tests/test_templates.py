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
    """dispatchSearch/search 不应累积 htmx:afterSettle 监听器（结果页已有 hx-on 处理滚动）"""
    tree_js = _read_static("components/tree.js")
    search_js = _read_static("components/search.js")
    assert "addEventListener('htmx:afterSettle'" not in tree_js, "tree.js 不应累积滚动监听器"
    assert "addEventListener('htmx:afterSettle'" not in search_js, "search.js 不应累积滚动监听器"


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
