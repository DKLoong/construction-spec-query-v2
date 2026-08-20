"""HTML 残留清理工具测试"""
from app.ai.text_clean import strip_html


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


def test_character_entity_decoded():
    """HTML 实体应被解码为对应字符"""
    result = strip_html("强度&nbsp;等级")
    assert "强度 等级" in result.replace(" ", " ")
