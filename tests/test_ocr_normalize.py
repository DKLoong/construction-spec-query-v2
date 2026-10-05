"""存储期规范化（spec §8.6 方案 b / §10.4，计划 Task 1）。

两个判据（spec §10.3 的「步 2」）：
- 规范化**不改行数** ⇒ 不可能改变 `parse_markdown` 的切分
- 在**真实夹具**上：规范化前后 `clause_no` 序列**完全相同**
"""
from pathlib import Path

import pytest

from app.parser.md_parser import parse_markdown
from app.parser.ocr_clean import normalize_md

REPO = Path(__file__).resolve().parent.parent
REF_MD = REPO / "data" / "_ocr_reference" / "reference.md"
BS = chr(92)                      # 反斜杠：不写字面两字符，避开各处转义
LIT_N = BS + "n"


def test_table_without_align_gets_centered():
    assert normalize_md("<table><tr><td>a</td></tr></table>") == \
        '<table align="center"><tr><td>a</td></tr></table>'


def test_table_with_attributes_keeps_them():
    out = normalize_md('<table class="x" style="width:100%"><tr></tr></table>')
    assert out.startswith('<table align="center"')
    assert 'class="x"' in out and 'style="width:100%"' in out


def test_table_already_centered_is_untouched():
    """已带 align= 或 margin auto 的表格不动 —— 这是幂等的前提"""
    a = '<table align="center"><tr></tr></table>'
    b = '<table style="margin-left:auto;margin-right:auto"><tr></tr></table>'
    assert normalize_md(a) == a
    assert normalize_md(b) == b


def test_literal_backslash_n_becomes_br():
    assert normalize_md("甲" + LIT_N + "乙") == "甲<br>乙"


def test_normalize_is_idempotent():
    src = "<table><tr><td>甲" + LIT_N + "乙</td></tr></table>"
    once = normalize_md(src)
    assert normalize_md(once) == once


def test_normalize_never_changes_line_count():
    src = "<table><tr><td>甲</td></tr></table>\n3.0.1 条文" + LIT_N + "续"
    assert normalize_md(src).count("\n") == src.count("\n")


def _require_ref() -> None:
    if REF_MD.is_file():
        return
    import os
    if os.environ.get("REQUIRE_LAYOUT_FIXTURE") == "1":
        pytest.fail("缺少真实夹具：P2 验收不得空过")
    pytest.skip("缺少真实夹具 data/_ocr_reference/reference.md")


def test_fixture_has_tables_to_normalize():
    """前置护栏：夹具里确实有表格。否则下一条用例是**空转恒绿**。"""
    _require_ref()
    assert "<table" in REF_MD.read_text(encoding="utf-8")


def test_clause_sequence_unchanged_on_real_fixture():
    """★ 在真实夹具上：规范化前后 clause_no 序列必须**逐个相同**（spec §10.3 步 2）"""
    _require_ref()
    md = REF_MD.read_text(encoding="utf-8")
    before = [c["clause_no"] for c in parse_markdown(md)]
    after = [c["clause_no"] for c in parse_markdown(normalize_md(md))]
    assert after == before
    assert len(before) > 100, f"夹具条文数异常：{len(before)}"
