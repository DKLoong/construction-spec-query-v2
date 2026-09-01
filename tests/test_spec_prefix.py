"""规范编号前缀共享模块测试"""
import pytest
from app.parser.spec_prefix import (
    normalize_spec_code, detect_hierarchy, detect_nature, PREFIX_WHITELIST, RECOMMENDED_T,
)


# ── normalize_spec_code 机械规范化 ──

def test_normalize_fullwidth_dash():
    assert normalize_spec_code("GB 50010—2010") == "GB 50010-2010"


def test_normalize_fullwidth_connect():
    assert normalize_spec_code("GB 50010－2010") == "GB 50010-2010"


def test_normalize_recommended_prefix_gbt():
    """GBT 无斜杠写法 → GB/T 标准写法（D20 复用判别知识）"""
    assert normalize_spec_code("GBT 50010-2010") == "GB/T 50010-2010"


def test_normalize_recommended_prefix_jgjt():
    assert normalize_spec_code("JGJT 162-2008") == "JGJ/T 162-2008"


def test_normalize_recommended_prefix_dbt():
    assert normalize_spec_code("DBT 11/1234-2020") == "DB/T 11/1234-2020"


def test_normalize_already_standard_idempotent():
    """已规范写法幂等不改"""
    assert normalize_spec_code("GB/T 50107-2010") == "GB/T 50107-2010"


def test_normalize_extra_spaces_compressed():
    assert normalize_spec_code("GB   50010   -  2010") == "GB 50010-2010"


def test_normalize_empty():
    assert normalize_spec_code("") == ""
    assert normalize_spec_code("  ") == ""


# ── detect_hierarchy / detect_nature 回归 ──

def test_hierarchy_gt():
    assert detect_hierarchy("GBT 50010-2010") == "国家标准"


def test_hierarchy_jgj():
    assert detect_hierarchy("JGJ 162-2008") == "建筑工程"


def test_hierarchy_longest_prefix_first():
    assert detect_hierarchy("JTG D40-2011") == "公路工程"
    assert detect_hierarchy("JT T 001-2012") == "交通运输"


def test_nature_gb_is_mandatory():
    assert detect_nature("GB 50010-2010") == "强制性"


def test_nature_gbt_recommended():
    assert detect_nature("GB/T 50107-2010") == "推荐性"


def test_nature_gbt_no_slash_recommended():
    """GBT 无斜杠写法仍识别为推荐性（用户常写 GBT）"""
    assert detect_nature("GBT 50107-2010") == "推荐性"


def test_nature_jgjt_recommended_fix():
    """修复：JGJT 旧实现漏判为强制性，现应为推荐性"""
    assert detect_nature("JGJ/T 231-2010") == "推荐性"


def test_nature_yz_recommended():
    assert detect_nature("YZ 5002-2015") == "推荐性"


def test_prefix_whitelist_contains_legacy():
    assert "GB" in PREFIX_WHITELIST and "GBT" in PREFIX_WHITELIST
    assert "JGJT" in PREFIX_WHITELIST and "DBT" in PREFIX_WHITELIST
    assert len(RECOMMENDED_T) == 9
