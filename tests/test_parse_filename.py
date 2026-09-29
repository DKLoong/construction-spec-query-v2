"""文件名 → 规范编号/名称 自动识别测试

导入场景：常规文件名即为规范名称（如 'GB/T 50010-2010 混凝土结构设计规范.pdf'），
选择文件后自动识别编号与名称并回填表单；不符合通用命名格式则跳过自动填充。
"""
from app.routes.import_routes import _parse_filename_to_code_title


# ═══════════════════════════════════════════
# 核心识别函数单元测试
# ═══════════════════════════════════════════

def test_parse_standard_with_space_and_dash():
    """国标推荐性：GB/T 50010-2010 + 名称"""
    code, title, matched = _parse_filename_to_code_title(
        "GB/T 50010-2010 混凝土结构设计规范.pdf"
    )
    assert matched is True
    assert code == "GB/T 50010-2010"
    assert title == "混凝土结构设计规范"


def test_parse_no_separator_between_code_and_name():
    """编号与名称无分隔：GB50010-2010混凝土结构设计规范"""
    code, title, matched = _parse_filename_to_code_title(
        "GB50010-2010混凝土结构设计规范.pdf"
    )
    assert matched is True
    assert code == "GB 50010-2010"
    assert title == "混凝土结构设计规范"


def test_parse_industry_standard():
    """行标强制性：JGJ 107-2016 + 名称"""
    code, title, matched = _parse_filename_to_code_title(
        "JGJ 107-2016 钢筋机械连接技术规程.pdf"
    )
    assert matched is True
    assert code == "JGJ 107-2016"
    assert title == "钢筋机械连接技术规程"


def test_parse_recommended_with_slash_t():
    """行标推荐性：JGJ/T 107-2010 + 名称，斜杠 T 应保留"""
    code, title, matched = _parse_filename_to_code_title(
        "JGJ/T 107-2010 钢筋机械连接技术规程.pdf"
    )
    assert matched is True
    assert code == "JGJ/T 107-2010"
    assert title == "钢筋机械连接技术规程"


def test_parse_without_year():
    """无年份编号：GB 50010 混凝土结构设计规范"""
    code, title, matched = _parse_filename_to_code_title(
        "GB 50010 混凝土结构设计规范.pdf"
    )
    assert matched is True
    assert code == "GB 50010"
    assert title == "混凝土结构设计规范"


def test_parse_code_only_with_year():
    """仅编号+年份（无名称）：GB 50010-2010"""
    code, title, matched = _parse_filename_to_code_title("GB 50010-2010.pdf")
    assert matched is True
    assert code == "GB 50010-2010"
    assert title == ""


def test_unmatched_pure_name():
    """纯名称无编号：不符合通用格式，跳过"""
    code, title, matched = _parse_filename_to_code_title("混凝土结构设计规范.pdf")
    assert matched is False
    assert code == ""
    assert title == ""


def test_unmatched_random_file():
    """无意义文件名：跳过"""
    code, title, matched = _parse_filename_to_code_title("新建文档.pdf")
    assert matched is False


def test_unmatched_db_regional_prefix():
    """DB 地方标准含地区号（DB13/T）非本系统通用格式，应跳过而非误解析"""
    code, title, matched = _parse_filename_to_code_title(
        "DB13/T 1234-2018 装配式建筑评价标准.pdf"
    )
    assert matched is False


def test_unmatched_no_extension_weird_name():
    """年份开头等异常命名：跳过"""
    code, title, matched = _parse_filename_to_code_title("2023年标准文件.pdf")
    assert matched is False


# ═══════════════════════════════════════════
# 容错增强（2026-09-29）：1 位序号 / 全角与破折号族归一 / 前后置噪声剥离 / 团体标准
# ═══════════════════════════════════════════

def test_parse_single_digit_serial_number():
    """序号仅 1 位：CJJ·JGJ·GBZ 的序号常态就是 1 位（旧正则 \\d{2,5} 漏判）"""
    for fname, want_code in [
        ("CJJ2-2008城市桥梁工程施工与质量验收规范.pdf", "CJJ 2-2008"),
        ("CJJ 2-2008 城市桥梁工程施工与质量验收规范.pdf", "CJJ 2-2008"),
        ("CJJ1-2008 城镇道路工程施工与质量验收规范.pdf", "CJJ 1-2008"),
        ("JGJ 1-2014 装配式混凝土结构技术规程.pdf", "JGJ 1-2014"),
        ("GBZ 1-2010 工业企业设计卫生标准.pdf", "GBZ 1-2010"),
    ]:
        code, title, matched = _parse_filename_to_code_title(fname)
        assert matched is True, f"{fname} 应可识别"
        assert code == want_code, f"{fname}: {code!r}"
        # 年份不得被当成名称的开头（年份被吞会直接改变编号身份）
        assert title and not title[0].isdigit(), f"{fname}: 年份被拼进名称 -> {title!r}"


def test_parse_recommended_variant_without_slash_is_normalized():
    """无斜杠推荐变体：GBT50353-2013 → GB/T 50353-2013（编号须过 normalize_spec_code）"""
    code, title, matched = _parse_filename_to_code_title(
        "GBT50353-2013建筑工程建筑面积计算规范.pdf"
    )
    assert matched is True
    assert code == "GB/T 50353-2013"
    assert title == "建筑工程建筑面积计算规范"


def test_parse_fullwidth_letters_and_dashes():
    """全角字母/斜杠 + 全角破折号：ＧＢ／Ｔ 50010—2010 → GB/T 50010-2010"""
    code, title, matched = _parse_filename_to_code_title(
        "ＧＢ／Ｔ 50010—2010 混凝土结构设计规范.pdf"
    )
    assert matched is True
    assert code == "GB/T 50010-2010"
    assert title == "混凝土结构设计规范"


def test_parse_all_dash_variants_keep_year():
    """破折号族全变体都不许吞年份。

    漏掉任一变体 → code 变成 'GB 50010' 且年份被拼进名称（不报错、照常入库），
    而 code 是全系统身份键，同一本规范会被分裂成两条记录。
    """
    for ch in "-‐‑‒–—―⁃﹘﹣－":
        fname = f"GB 50010{ch}2010 混凝土结构设计规范.pdf"
        got = _parse_filename_to_code_title(fname)
        assert got == ("GB 50010-2010", "混凝土结构设计规范", True), \
            f"U+{ord(ch):04X} 未归一 -> {got!r}"


def test_parse_lead_ordinal_and_noise_word_stripped():
    """前置噪声：数字序号（1. / 2） / 3、）与噪声词（扫描件_）"""
    for fname, want_code in [
        ("1. GB 50010-2010 混凝土结构设计规范.pdf", "GB 50010-2010"),
        ("2）GB 50010-2010 混凝土结构设计规范.pdf", "GB 50010-2010"),
        ("3、GB 50010-2010 混凝土结构设计规范.pdf", "GB 50010-2010"),
        ("扫描件_GB50204-2015混凝土结构工程施工质量验收规范.pdf", "GB 50204-2015"),
    ]:
        code, title, matched = _parse_filename_to_code_title(fname)
        assert matched is True and code == want_code, f"{fname}: {(code, matched)!r}"
        assert title, f"{fname}: 名称不应为空"


def test_parse_tail_noise_stripped():
    """尾部噪声：纯数字括号 / _OCR / -副本"""
    for fname in [
        "GB 50010-2010 混凝土结构设计规范(1).pdf",
        "GB 50010-2010 混凝土结构设计规范（2）.pdf",
        "GB 50010-2010 混凝土结构设计规范_OCR.pdf",
        "GB 50010-2010 混凝土结构设计规范-副本.pdf",
    ]:
        got = _parse_filename_to_code_title(fname)
        assert got == ("GB 50010-2010", "混凝土结构设计规范", True), f"{fname}: {got!r}"


def test_parse_keeps_legit_parenthesised_suffix():
    """反向守卫：名称里的（2015年版）/（二）不是噪声，左右括号都不得削掉。

    旧实现 `title.strip("（）()")` 会把括号当分隔符无差别削 → '2015年版）混凝土…'（悬空右括号）
    或 '…（2016年版'（丢右括号）。本用例锁死该行为不被回退。
    """
    for fname, want_title in [
        ("GB 50010-2010（2015年版）混凝土结构设计规范.pdf", "（2015年版）混凝土结构设计规范"),
        ("GB 50011-2010 建筑抗震设计规范（2016年版）.pdf", "建筑抗震设计规范（2016年版）"),
        ("GB 50011-2010 工程结构设计基本术语标准（二）.pdf", "工程结构设计基本术语标准（二）"),
    ]:
        code, title, matched = _parse_filename_to_code_title(fname)
        assert matched is True, f"{fname} 应可识别（code={code!r}）"
        assert title == want_title, f"{fname}: {title!r}"


def test_parse_association_standard_with_slash_prefix():
    """团体标准 T/CECS、T/CBDA：斜杠后是协会代号而非 /T"""
    for fname, want_code in [
        ("T/CECS 1234-2020 某协会标准.pdf", "T/CECS 1234-2020"),
        ("T/CBDA 1-2016 某协会标准.pdf", "T/CBDA 1-2016"),
    ]:
        code, title, matched = _parse_filename_to_code_title(fname)
        assert matched is True and code == want_code, f"{fname}: {(code, matched)!r}"
        assert title == "某协会标准"


def test_unmatched_bare_t_q_with_single_digit():
    """裸 Q/T + 1 位序号：施工场景的栋号（T1/T2/T3）与季度（Q1~Q4）命名，必须不匹配。

    序号放宽到 1 位后，若无此守卫会把 `T2塔楼施工方案` 回填成编号 `T 2`。
    """
    for fname in ["T1图纸.pdf", "T2塔楼施工方案.pdf", "T3施工方案.pdf",
                  "Q1报表.pdf", "Q2季度总结.pdf"]:
        code, title, matched = _parse_filename_to_code_title(fname)
        assert matched is False, f"{fname} 不应识别为 {(code, title)!r}"


def test_unmatched_legit_names_not_touched_by_noise_rules():
    """反向守卫：噪声规则不得让原本不匹配的名字变成匹配"""
    for fname in ["混凝土结构设计规范.pdf", "新建文档.pdf",
                  "2023年标准文件.pdf", "1.5倍安全系数.pdf",
                  "3D打印混凝土技术规程.pdf"]:
        code, title, matched = _parse_filename_to_code_title(fname)
        assert matched is False, f"{fname} 不应识别为 {(code, title)!r}"


# ═══════════════════════════════════════════
# 接口测试
# ═══════════════════════════════════════════

def test_parse_filename_endpoint_matched(auth_client):
    """POST /import/parse-filename 命中通用格式返回 code/title"""
    resp = auth_client.post(
        "/import/parse-filename",
        data={"filename": "GB 50010-2010 混凝土结构设计规范.pdf"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is True
    assert data["code"] == "GB 50010-2010"
    assert data["title"] == "混凝土结构设计规范"


def test_parse_filename_endpoint_unmatched(auth_client):
    """POST /import/parse-filename 未命中返回 matched=False"""
    resp = auth_client.post(
        "/import/parse-filename",
        data={"filename": "某个文件.pdf"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is False
    assert data["code"] == ""
    assert data["title"] == ""
