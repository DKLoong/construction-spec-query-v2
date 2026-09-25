"""分类输入框的聚焦悬浮提示（T6）

用户要求：修改规范分类、条文分类时，聚焦输入框在**其下方**出现等宽悬浮提示，
提示「多个分类用半角标点隔开」，失焦消失；风格参考参数设置模块。

这条提示不是画饼——多值分类确实被支持：检索端按 `LIKE %值%` 子串匹配
（app/search/sql_search.py 的 _add_multi），分类树也会按逗号拆成独立节点
（app/routes/import_routes.py 的 _build_tree_nodes）。

行为（聚焦显示、位置在输入框下方、失焦隐藏、滚动跟随）由 UI 探针
scripts/probe_spec_ui.py 的 f5 覆盖；此处锁定模板与样式的契约。
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
PARTIALS = APP / "templates" / "partials"
STATIC = APP.parent / "static"

HINT_TEXT = '如需设置多个分类，请用半角标点(英文标点)","隔开。'


def _partial(name: str) -> str:
    return (PARTIALS / name).read_text(encoding="utf-8")


def _input_tag(html: str, name: str) -> str:
    m = re.search(rf'<input[^>]*name="{name}"[^>]*>', html, re.S)
    assert m, f"未找到 name={name} 的输入框"
    return m.group(0)


# ═══════════════════════════════════════════
# 模板契约：哪些输入框要带提示
# ═══════════════════════════════════════════

def test_spec_class_inputs_carry_hint():
    """规范分类的 dim2/dim3 输入框须带 data-hint（提示多值写法）"""
    html = _partial("spec_class_edit.html")
    for name in ("dim2_stage", "dim3_usage"):
        tag = _input_tag(html, name)
        assert "data-hint=" in tag, f"{name} 缺少悬浮提示绑定"
        assert HINT_TEXT in tag, f"{name} 的提示文案不符"


def test_clause_class_inputs_carry_hint():
    """条文分类的 dim4/dim5/dim6 输入框须带 data-hint"""
    html = _partial("clause_class_edit.html")
    for name in ("dim4_specialty", "dim5_location", "dim6_material"):
        tag = _input_tag(html, name)
        assert "data-hint=" in tag, f"{name} 缺少悬浮提示绑定"
        assert HINT_TEXT in tag, f"{name} 的提示文案不符"


def test_hint_not_bound_to_non_class_inputs():
    """非分类字段不该被打上分类提示（避免提示语义外溢）

    分栏编辑页的条文号/标题/正文都不是「可多值」的分类字段，提示挂在那里会误导。
    """
    html = _partial("clause_edit_page.html")
    assert "data-hint" not in html, \
        "分栏编辑页的条文号/标题不是分类字段，不应绑定分类提示"


# ═══════════════════════════════════════════
# 容器与样式
# ═══════════════════════════════════════════

def test_hint_container_present_and_loaded():
    """base.html 须挂载提示容器并引入脚本"""
    base = (APP / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'id="field-hint-pop"' in base, "缺少悬浮提示容器"
    assert "field-hint.js" in base, "未引入 field-hint.js"
    assert (STATIC / "components" / "field-hint.js").is_file(), "field-hint.js 不存在"


def test_hint_style_is_monospace_and_matches_params_look():
    """提示框须等宽字体，且沿用参数设置 .param-pop 的视觉（同一套观感）

    参照物 `.param-pop` 在 params_panel.html 的**内联样式块**里（不在 app.css），
    故两边分别读取、只比对真正该一致的那一项：底色。
    """
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    m = re.search(r"\.field-hint-pop\s*\{([^}]*)\}", css, re.S)
    assert m, "app.css 缺少 .field-hint-pop 规则"
    block = m.group(1)
    assert "font-family" in block and "monospace" in block, \
        f"提示框未设等宽字体（半角/全角标点的差别要看得清）：{block}"

    panel = _partial("params_panel.html")
    m_ref = re.search(r"\.param-pop\s*\{([^}]*)\}", panel, re.S)
    assert m_ref, "参照物 .param-pop 不存在（参数设置模块的提示样式）"

    def _bg(text):
        found = re.search(r"background:\s*([^;]+);", text)
        return found.group(1).strip() if found else None

    ref_bg, my_bg = _bg(m_ref.group(1)), _bg(block)
    assert ref_bg and my_bg, f"缺少背景色（会与表格背景糊在一起）：ref={ref_bg} mine={my_bg}"
    assert ref_bg == my_bg, f"提示框底色与参数设置模块不一致：{my_bg} != {ref_bg}"
