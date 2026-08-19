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
