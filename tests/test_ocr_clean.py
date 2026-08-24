"""OCR 文本保守清洗工具测试（app/parser/ocr_clean.py）"""
from app.parser.ocr_clean import clean_ocr_text


# ═══════════════════════════════════════════
# 规则1：孤立页码行删除
# ═══════════════════════════════════════════

def test_removes_plain_page_number_line():
    """独立成行的「第 1 页」应被删除"""
    md = "第 1 页\n\nGB/T 1499.1—2017"
    result = clean_ocr_text(md)
    assert "第 1 页" not in result
    assert "GB/T 1499.1—2017" in result


def test_removes_compact_page_number_line():
    """无空格「第1页」应被删除"""
    md = "第1页\n内容"
    assert clean_ocr_text(md) == "内容"


def test_removes_fullwidth_space_page_line():
    """全角空格「第　3　页」应被删除"""
    md = "第　3　页\n内容"
    assert clean_ocr_text(md) == "内容"


def test_keeps_hash_page_marker():
    """OCR 结构分隔标记「## 第2页」应保留（供封面过滤/人工审查识别页面边界）"""
    md = "## 第2页\n\n正文内容"
    assert clean_ocr_text(md) == md


def test_keeps_line_containing_page_word_in_sentence():
    """非独立页码行（页码词嵌在句子里）不应被误删"""
    md = "本标准共 10 页，其中第 5 页为附录。"
    assert clean_ocr_text(md) == md


# ═══════════════════════════════════════════
# 规则2：纯页码数字行删除
# ═══════════════════════════════════════════

def test_removes_pure_number_lines():
    """独立成行的 1-3 位纯数字应被删除"""
    md = "1\n12\n123\n正文"
    result = clean_ocr_text(md)
    assert "1" not in result.split("正文")[0]
    assert "正文" in result


def test_keeps_numbered_clause_line():
    """条文编号行（1.0.1 正文）不应被当作纯数字行删除"""
    md = "1.0.1  为在混凝土结构中使用钢筋机械连接，制定本规程。"
    assert clean_ocr_text(md) == md


# ═══════════════════════════════════════════
# 规则3：OCR 失败标记行删除
# ═══════════════════════════════════════════

def test_removes_ocr_failure_marker():
    """[OCR 失败: xxx] 标记行应被删除"""
    md = "## 第1页\n\n_[OCR 失败: ConnectionError]_\n\n正文"
    result = clean_ocr_text(md)
    assert "[OCR 失败" not in result
    assert "正文" in result


# ═══════════════════════════════════════════
# 规则4：完全重复的短页眉行删除
# ═══════════════════════════════════════════

def test_removes_repeated_short_header():
    """整行重复 ≥3 次的短行（如每页顶部标准号）应被删除"""
    md = "GB/T 1499.1—2017\n正文A\nGB/T 1499.1—2017\n正文B\nGB/T 1499.1—2017\n正文C"
    result = clean_ocr_text(md)
    assert "GB/T 1499.1—2017" not in result
    assert "正文A" in result


def test_keeps_short_line_appearing_once():
    """仅出现一次的短行不应被删除"""
    md = "GB/T 1499.1—2017\n正文"
    assert clean_ocr_text(md) == md


def test_keeps_repeated_sentence_like_line():
    """重复但像句子的行（含句末标点）不应被当作页眉删除"""
    md = "本标准自发布之日起实施。\n正文A\n本标准自发布之日起实施。\n正文B\n本标准自发布之日起实施。\n正文C"
    result = clean_ocr_text(md)
    assert "本标准自发布之日起实施。" in result


# ═══════════════════════════════════════════
# 保守性 & 幂等性
# ═══════════════════════════════════════════

def test_normal_text_unchanged():
    """正常条文文本完全不受影响"""
    md = "1.0.1  为在混凝土结构中使用钢筋机械连接，制定本规程。\n\n钢筋进场时应抽取试件检验。"
    assert clean_ocr_text(md) == md


def test_clean_is_idempotent():
    """清洗应幂等：重复清洗结果不变"""
    md = "第 1 页\n## 第2页\n12\nGB/T 1499.1—2017\n正文"
    once = clean_ocr_text(md)
    twice = clean_ocr_text(once)
    assert once == twice


def test_empty_input():
    assert clean_ocr_text("") == ""
    assert clean_ocr_text(None) == ""
