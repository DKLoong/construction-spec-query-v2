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
