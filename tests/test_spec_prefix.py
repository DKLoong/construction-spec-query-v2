"""规范编号前缀共享模块测试"""
import pytest
from app.parser.spec_prefix import (
    normalize_spec_code, detect_hierarchy, detect_nature, detect_industry,
    PREFIX_WHITELIST, RECOMMENDED_T,
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
    """JGJ 归行业标准（层级语义；行业归属由 detect_industry 单独给出）"""
    assert detect_hierarchy("JGJ 162-2008") == "行业标准"


def test_hierarchy_longest_prefix_first():
    assert detect_hierarchy("JTG D40-2011") == "行业标准"
    assert detect_hierarchy("JT T 001-2012") == "行业标准"


def test_industry_jgj_building():
    """JGJ（建筑工程行业）行业检测"""
    assert detect_industry("JGJ 162-2008") == "建筑工程"


def test_industry_jtg_highway():
    assert detect_industry("JTG D40-2011") == "公路工程"
    assert detect_industry("JT T 001-2012") == "交通运输"


def test_industry_gb_empty():
    """国家标准无特定行业归属"""
    assert detect_industry("GB 50010-2010") == ""
    assert detect_industry("DB13/T 1234") == ""


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


def test_nature_association_standard_recommended():
    """团体标准 T/CECS、T/CBDA 为推荐性。

    旧实现先 `c = code.replace("/", "")` → 'TCECS 1234-2020'，`^T($|\\s|\\d)` 再也匹配不到
    「T 后跟 /」，于是落到兜底分支返回「强制性」——与「团体标准一律推荐性」的常识相反。
    团体标准在工程建设领域很常见，且文件名自动识别现已支持 T/CECS / T/CBDA
    （见 import_routes._parse_filename_to_code_title），此处必须同口径。
    """
    assert detect_nature("T/CECS 1234-2020") == "推荐性"
    assert detect_nature("T/CBDA 1-2016") == "推荐性"


def test_nature_bare_t_still_recommended():
    """裸 T（无斜杠）写法不受本次修复影响"""
    assert detect_nature("T 1-2010") == "推荐性"
    assert detect_nature("T 12345-2010") == "推荐性"


def test_nature_enterprise_standard_has_no_nature():
    """企业标准（Q/…、Q …）无强制/推荐之分，返回空串"""
    assert detect_nature("Q/SY 1234-2020") == ""
    assert detect_nature("Q 1234-2020") == ""


def test_prefix_whitelist_contains_legacy():
    assert "GB" in PREFIX_WHITELIST and "GBT" in PREFIX_WHITELIST
    assert "JGJT" in PREFIX_WHITELIST and "DBT" in PREFIX_WHITELIST
    assert len(RECOMMENDED_T) == 9
