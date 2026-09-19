"""embedding 文本构造测试（build_embed_text 与旧拼接格式一致）"""
from app.search.embed_text import build_embed_text


def test_build_embed_text_full():
    """完整字段拼接结果与旧格式逐字节一致"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容") == \
        "GB 50010 混凝土规范 [5.1.1] 模板 内容"


def test_build_embed_text_matches_legacy_format():
    """非空字段下与导入/维护路由旧拼接格式一致"""
    code, spec_title, clause_no, title, content = "GB 50010", "混凝土规范", "5.1.1", "模板", "内容"
    legacy = f"{code or ''} {spec_title or ''} [{clause_no}] {title or ''} {content}"
    assert build_embed_text(code, spec_title, clause_no, title, content) == legacy


def test_build_embed_text_empty_fields_no_extra_space():
    """空 title/content 末尾 strip，不残留多余空格"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "", "") == \
        "GB 50010 混凝土规范 [5.1.1]"
    # 空 code/spec_title 开头无多余空格
    assert build_embed_text("", "", "5.1.1", "模板", "内容") == "[5.1.1] 模板 内容"


def test_build_embed_text_strips_markup_residue():
    """含 PaddleOCR-VL 标记的 content 不得把标记喂进 embedding 模型"""
    content = (
        '<div style="text-align: center;">表3.0.5 接头极限抗拉强度</div>'
        '<table border=1><tr><td>接头等级</td><td colspan="2">连接件型式</td></tr></table>'
        "抗拉强度 $ N/mm^{{2}} $ 应符合要求"
    )
    text = build_embed_text("JGJ 107", "钢筋机械连接技术规程", "3.0.5", "", content)
    assert "<" not in text and "$" not in text
    for bad in ("td", "style", "div", "table", "border", "colspan", "N/mm"):
        assert bad not in text, f"embedding 文本残留标记: {bad}"
    assert "接头极限抗拉强度" in text
    assert "接头等级" in text
    assert "连接件型式" in text
    assert "应符合要求" in text


def test_build_embed_text_plain_content_unchanged():
    """无标记 content 的 embedding 文本逐字节不变（保证既有向量不失效）"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板",
                            "套筒是钢筋机械连接的关键部件。") == \
        "GB 50010 混凝土规范 [5.1.1] 模板 套筒是钢筋机械连接的关键部件。"
