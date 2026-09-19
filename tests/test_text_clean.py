"""HTML 残留清理工具测试"""
import re

from app.ai.text_clean import plain_text, strip_html
from app.search.tokenize import tokenize


def test_removes_div_tag_keeps_text():
    """<div> 标签应被剥离，标签内文本保留"""
    text = '接头应符合规定 <div style="text-align: center">表 3.0.7</div>'
    result = strip_html(text)
    assert "表 3.0.7" in result
    assert "<div" not in result


def test_br_becomes_newline():
    """<br> 应转换为换行，且行间文本独立"""
    text = "第一条要求<br>第二条要求"
    result = strip_html(text)
    assert "第一条要求" in result
    assert "第二条要求" in result
    assert "\n" in result


def test_table_tags_stripped():
    """表格标签剥离后仅保留单元格文本"""
    text = "<table><tr><td>强度等级</td><td>C30</td></tr></table>"
    result = strip_html(text)
    assert "强度等级" in result
    assert "C30" in result
    assert "<table" not in result and "<td" not in result


def test_empty_string():
    assert strip_html("") == ""
    assert strip_html(None) == ""


def test_plain_text_unchanged():
    text = "钢筋机械连接接头的性能等级分为Ⅰ、Ⅱ、Ⅲ级。"
    assert strip_html(text) == text


# ---------- plain_text：派生文本专用（去标记 + 丢 LaTeX + 单元格分隔）----------

# 取自真实 PaddleOCR-VL 输出的表格片段（clause 391 形态）
_TABLE_HTML = (
    "I 级、Ⅱ级、Ⅲ级接头的极限抗拉强度必须符合表3.0.5的规定。\n"
    '<div style="text-align: center;">表3.0.5 接头极限抗拉强度</div>\n'
    "<table border=1 style='margin: auto; word-wrap: break-word;'>"
    "<tr><td style='text-align: center; word-wrap: break-word;'>接头等级</td>"
    "<td colspan=\"2\">连接件型式</td></tr>"
    "<tr><td rowspan=\"3\">Ⅰ级</td><td>$ f_{{mst}}^{{0}} \\ge f_{{stk}} $</td></tr>"
    "</table>"
    "<img src='imgs/img_in_image_box_168_501_648_755.jpg' alt=\"Image\" width=\"60%\" />"
)


def test_plain_text_drops_latex_span():
    """LaTeX 数学段应整段丢弃，其外文字保留"""
    result = plain_text("抗拉强度 $ N/mm^{{2}} $ 应符合要求")
    assert "抗拉强度" in result
    assert "应符合要求" in result
    assert "$" not in result
    assert "N/mm" not in result


def test_plain_text_drops_latex_with_entity_and_relation():
    """含实体与关系符的公式段同样整段丢弃"""
    result = plain_text("残余变形 $ u_0 \\le 0.16 (d &gt; 32) $ 为合格")
    assert "残余变形" in result
    assert "为合格" in result
    assert "gt" not in result and "le" not in result


def test_plain_text_separates_table_cells():
    """相邻单元格文字必须分隔，不得粘连成「接头类型连接件型式」"""
    result = plain_text("<table><tr><td>接头类型</td><td>连接件型式</td></tr></table>")
    assert "接头类型连接件型式" not in result
    assert re.search(r"接头类型\s+连接件型式", result)


def test_plain_text_real_table_leaves_no_markup_tokens():
    """真实表格 HTML 清洗后，分词结果里不得出现标记 token"""
    result = plain_text(_TABLE_HTML)
    tokens = {w.lower() for w in tokenize(result)}
    assert not (tokens & {"td", "tr", "style", "table", "div", "center", "img",
                          "src", "alt", "image", "colspan", "rowspan", "border",
                          "margin", "auto", "text", "align", "wrap", "break"})
    assert "极限抗拉强度" in result
    assert "接头等级" in result
    assert "连接件型式" in result


def test_plain_text_is_idempotent():
    """已清洗文本再次清洗应原样返回（保证非标记条文索引不被改动）"""
    plain = "钢筋机械连接接头的性能等级分为Ⅰ、Ⅱ、Ⅲ级。"
    assert plain_text(plain) == plain
    once = plain_text(_TABLE_HTML)
    assert plain_text(once) == once


def test_plain_text_keeps_engineering_literals():
    """不得误伤工程写法：牌号、强度等级、条文号、单位"""
    for literal in ["HRB400 钢筋", "C30 混凝土", "第 5.1.1 条", "N/mm2", "HRB400E"]:
        assert plain_text(literal) == literal


def test_plain_text_empty_and_markup_only():
    """空串与纯标记内容应归为空串（不破坏 build_search_text 的占位空格逻辑）"""
    assert plain_text("") == ""
    assert plain_text(None) == ""
    assert plain_text("<div></div>") == ""
    assert plain_text("<table><tr><td></td></tr></table>") == ""
    assert plain_text("<img src='a.jpg' alt=\"Image\" />") == ""


def test_character_entity_decoded():
    """HTML 实体应被解码为对应字符"""
    result = strip_html("强度&nbsp;等级")
    assert "强度 等级" in result.replace(" ", " ")
