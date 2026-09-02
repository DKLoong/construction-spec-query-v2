"""规范编号前缀知识共享模块（层级/性质/编号规范化，单一知识源）

抽取自 import_routes._detect_hierarchy / _detect_nature，供导入校核、检索、
版本识别统一引用。行为与旧实现保持一致，仅一处修复：推荐变体集合补 JGJT
（旧实现漏判，将 JGJ/T 误判为强制性）。
"""
import re

# 前缀匹配序（按长度降序，最长前缀优先匹配；已去斜杠写法）。
# value 为该前缀的「行业归属」（国标/地方标准无行业，值为空）：
# 规范「层级」与「行业」是两维——层级由 detect_hierarchy 按前缀组判定
# （国标→国家标准 / 地标→地方标准 / 其余行业前缀→行业标准），行业由 detect_industry 返回。
PREFIX_MAP: list[tuple[str, str]] = [
    ("JTGT", "公路工程"), ("JTG", "公路工程"),
    ("JTT", "交通运输"), ("JT", "交通运输"),
    ("JGT", "建筑工业"), ("JGJ", "建筑工程"), ("JG", "建筑工业"),
    ("CJT", "城镇建设"), ("CJ", "城镇建设"),
    ("JBT", "机械"), ("JB", "机械"),
    ("NYT", "农业"), ("NY", "农业"),
    ("GBT", ""), ("GB", ""),
    ("TB", "铁路"), ("MH", "民用航空"),
    ("YZ", "邮政"), ("DB", ""),  # 地方标准（带地区号，行业为空）
]

# 推荐性变体（无斜杠写法 → 标准带 /T 代号）。归一化与性质判别共用一份。
RECOMMENDED_T: dict[str, str] = {
    "GBT": "GB/T", "JTT": "JT/T", "JTGT": "JTG/T", "JGT": "JG/T",
    "CJT": "CJ/T", "JBT": "JB/T", "NYT": "NY/T", "DBT": "DB/T",
    "JGJT": "JGJ/T",
}

# 合法前缀白名单（含无推荐变体的 GBZ/JGJ/CJJ 等），供文件名解析校验
PREFIX_WHITELIST: set[str] = {
    "JTGT", "JTG", "JTT", "JT", "JGT", "JG", "JGJ", "CJT", "CJ",
    "JBT", "JB", "NYT", "NY", "GBT", "GB", "TB", "MH", "YZ",
    "DB", "DBT", "T", "Q", "CJJ", "GJB", "GBZ", "GBJ", "JJG",
    "JJF", "XB", "QC", "JGJT",
}

# 推荐变体集合（供 detect_nature 判断；keys 即合法推荐变体）
_RECOMMENDED_KEYS: set[str] = set(RECOMMENDED_T.keys())


def normalize_spec_code(code: str) -> str:
    """规范化规范编号：全角符号→半角、推荐变体补斜杠、空格规整。

    处理顺序：全角修正 → 推荐变体前缀补 /T → 压缩连续空格。
    示例：'GB 50010—2010' → 'GB 50010-2010'；'GBT 50010-2010' → 'GB/T 50010-2010'。
    """
    if not code:
        return ""
    c = code.strip()
    c = c.replace("—", "-").replace("－", "-").replace("–", "-").replace("　", " ")
    # 推荐变体前缀补斜杠：仅当行首纯字母段精确等于某推荐变体
    m = re.match(r"^([A-Za-z]+)", c)
    if m:
        letters = m.group(1).upper()
        if letters in _RECOMMENDED_KEYS:
            c = RECOMMENDED_T[letters] + c[m.end():]
    # 压缩连续空白为单个半角空格（代号 顺序号-年份 之间恰好一个空格）
    c = re.sub(r"\s+", " ", c).strip()
    # 去除连字符两侧空格（GB 50010 - 2010 → GB 50010-2010，空格规整）
    c = re.sub(r"\s*-\s*", "-", c)
    return c


def _hierarchy_of(prefix: str) -> str:
    """前缀 → 规范层级（国家标准/地方标准为独立层级，其余行业前缀归行业标准）"""
    if prefix in ("GB", "GBT"):
        return "国家标准"
    if prefix in ("DB", "DBT"):
        return "地方标准"
    return "行业标准"  # JGJ/JTG/CJ/JB/NY/TB/MH/YZ 等均为行业标准


def detect_hierarchy(code: str) -> str:
    """根据规范编号前缀判断规范层级（去斜杠后按最长前缀匹配）

    层级语义：GB→国家标准 / DB→地方标准 / JGJ/JTG 等→行业标准 / T→团体 / Q→企业。
    行业归属（JGJ 属于建筑工程等）由 detect_industry 返回，不再混入层级字段。
    """
    if not code:
        return ""
    c = code.replace("/", "").strip()
    if not c:
        return ""
    for prefix, _ in PREFIX_MAP:
        if c.startswith(prefix):
            return _hierarchy_of(prefix)
    if c.startswith("T"):
        return "团体标准"
    if c.startswith("Q"):
        return "企业标准"
    return ""


def detect_industry(code: str) -> str:
    """根据规范编号前缀判断规范所属行业（仅行业标准类有行业；国标/地标等行业为空）

    示例：JGJ 162-2008 → 建筑工程；JTG D40 → 公路工程；GB 50010 → 空。
    """
    if not code:
        return ""
    c = code.replace("/", "").strip()
    if not c:
        return ""
    for prefix, industry in PREFIX_MAP:
        if c.startswith(prefix):
            return industry or ""
    return ""


def detect_nature(code: str) -> str:
    """根据规范编号判断强制性/推荐性"""
    if not code:
        return ""
    c = code.replace("/", "").strip()
    if not c:
        return ""
    if c.startswith("Q"):
        return ""  # 企业标准无强制/推荐之分
    if re.match(r"^T($|\s|\d)", c):
        return "推荐性"  # 团体标准（T 后不能紧跟字母，以区分 TB）
    if c.startswith("YZ"):
        return "推荐性"  # 邮政标准始终推荐性
    m = re.match(r"^([A-Za-z]+)", c)
    if not m:
        return "强制性"
    letters = m.group(1)
    if letters in _RECOMMENDED_KEYS:
        return "推荐性"
    if "/T" in code:
        return "推荐性"  # DB13/T 这类非规范写法
    return "强制性"
