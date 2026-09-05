import pytest
from app.termdict.validation import validate_row, split_aliases


def test_split_aliases():
    assert split_aliases("砼, 混泥土 ,,混凝土") == ["砼", "混泥土", "混凝土"]
    assert split_aliases("") == []


def test_valid_row():
    # 别名不得与代表词互为子串：螺纹钢筋/光圆钢筋 均含代表词「钢筋」→ 换为不互为子串的词面
    data, err = validate_row("dim6", "钢筋", "钢筋", "螺纹钢,圆钢")
    assert err is None
    assert data["dimension"] == "dim6"
    assert data["aliases"] == "螺纹钢,圆钢"


def test_invalid_dimension():
    _, err = validate_row("dim1", "国家标准", "国家标准")
    assert err and "维度" in err


def test_blank_label_canonical():
    _, err = validate_row("dim6", " ", "钢筋")
    assert err and "标签" in err
    _, err = validate_row("dim6", "钢筋", "")
    assert err and "代表词" in err


def test_canonical_substring_with_alias_rejected():
    """本行内 canonical 与 alias 互为子串 → 拒（防 keyword 自命中干扰）。"""
    _, err = validate_row("dim6", "钢筋混凝土", "钢筋混凝土", "混凝土")
    assert err
    _, err = validate_row("dim6", "混凝土", "混凝土", "钢筋混凝土")
    assert err


def test_aliases_dedup_and_canonical_skipped():
    data, err = validate_row("dim6", "混凝土", "混凝土", "砼,混凝土,砼")
    assert err is None
    # canonical 本身不落入 aliases；重复别名去重
    assert data["aliases"] == "砼"


def test_source_default_manual():
    data, _ = validate_row("dim4", "结构", "结构")
    assert data["source"] == "manual"
