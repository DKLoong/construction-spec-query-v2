"""validate_row 的返回契约与行级校验规则（app/lexicon/validation.py）

**契约（此前是隐式的、无测试守护）**：`data` 与 `err` 互斥——
`err` 非空 ⟺ `data` 为 None。全项目约 10 处调用点写的都是

    data, err = validate_row(...)
    if err:
        return 400
    ... data["kind"] ...      # ← 此处假定 data 必为 dict

若契约被破坏（如将来新增一条 `return None, None` 或 `return data, "err"`），
那些调用点会抛 TypeError → HTTP 500，而类型检查器**看不到**这种相关性
（`tuple[dict | None, str | None]` 无法表达两个元素互斥），故必须有测试守住。

本文件把该隐式契约变成显式断言，并顺带覆盖各条行级校验规则。
"""
import pytest

from app.lexicon.validation import validate_row

# 覆盖**每一条**错误返回路径（漏一条就等于契约没测全）
INVALID_CASES = [
    ("bogus", "混凝土", "砼", "", "kind 不合法"),
    ("alias", "", "砼", "", "代表词为空"),
    ("alias", "   ", "砼", "", "代表词仅空白"),
    ("alias", "混凝土", "", "", "变体为空"),
    ("alias", "混凝土", "混凝土", "", "变体与代表词相同"),
    ("alias", "混凝土", "混凝土强度", "", "变体与代表词互为子串（变体含代表词）"),
    ("alias", "混凝土强度", "混凝土", "", "变体与代表词互为子串（代表词含变体）"),
    ("confusable", "沉降", "差异沉降", "", "confusable 缺区分说明"),
    ("confusable", "沉降", "差异沉降", "   ", "区分说明仅空白"),
    ("confusable", "沉降", "差异沉降,另一词", "范围不同", "confusable 变体须恰一个"),
    ("confusable", "沉降", "沉降", "范围不同", "confusable 词A与词B相同"),
    ("confusable", "沉降", "差异沉降", "范围不同", "confusable 词A与词B互为子串"),
]


@pytest.mark.parametrize("kind,canonical,variants,distinguish,reason", INVALID_CASES)
def test_invalid_path_returns_none_data(kind, canonical, variants, distinguish, reason):
    """每条错误路径都必须返回 (None, 非空错误信息)"""
    data, err = validate_row(kind, canonical, variants, distinguish)
    assert err, f"应报错：{reason}"
    assert data is None, f"报错时 data 必须为 None：{reason}"


@pytest.mark.parametrize("kind,canonical,variants,distinguish,reason", INVALID_CASES)
def test_data_and_error_are_mutually_exclusive(kind, canonical, variants, distinguish, reason):
    """契约：data 与 err 恰好一个非空（调用点依赖此性质）"""
    data, err = validate_row(kind, canonical, variants, distinguish)
    assert (data is None) != (err is None), f"data/err 互斥契约被破坏：{reason}"


def test_valid_path_returns_data_and_no_error():
    """成功路径返回 (dict, None)，且完成 strip / variants 规范化"""
    data, err = validate_row("alias", " 混凝土 ", " 砼 , 水泥 ", " ")
    assert err is None
    assert isinstance(data, dict)
    assert data == {"kind": "alias", "canonical": "混凝土",
                    "variants": "砼,水泥", "distinguish": ""}


def test_valid_confusable_path():
    """confusable 合法输入同样满足契约"""
    data, err = validate_row("confusable", "圈梁", "构造柱", " 竖向构件不同 ")
    assert err is None
    assert data == {"kind": "confusable", "canonical": "圈梁",
                    "variants": "构造柱", "distinguish": "竖向构件不同"}
