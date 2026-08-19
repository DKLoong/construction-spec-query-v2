"""搜索结果摘要安全截断测试"""

from app.routes.search_routes import _safe_summary


def test_safe_summary_keeps_short_content():
    """内容不超过 limit 时原样返回"""
    s = "无公式的短条文"
    assert _safe_summary(s, 200) == s


def test_safe_summary_truncates_long_content():
    """长内容截断到 limit 内"""
    s = "长内容" * 100
    out = _safe_summary(s, 200)
    assert len(out) <= 200


def test_safe_summary_closes_orphan_formula():
    """截断点落在未闭合公式内 → 回退到最后一个 $ 之前，保证 $ 成对"""
    s = "条文说明 $ 5 \\, m $ 剩余内容" + "x" * 300
    out = _safe_summary(s, 200)
    assert out.count("$") % 2 == 0


def test_safe_summary_keeps_even_dollar():
    """截断后 $ 为偶数 → 原样截断"""
    s = ("$ 5 m $" * 30) + "xx"
    out = _safe_summary(s, 200)
    assert out.count("$") % 2 == 0


def test_safe_summary_returns_original_at_exact_limit():
    """内容恰好等于 limit → 不截断、$ 保持成对"""
    s = "$ a $" + "x" * 195  # len(s) == 200
    assert len(s) == 200
    assert _safe_summary(s, 200) == s
