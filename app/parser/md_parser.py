import re


# 编号行前缀正则（按匹配优先级排列）
# 点号兼容半角 `.` 与全角 `．`（U+FF0E，OCR 高频把条文号点误识别为全角）
_NUM_PATTERNS = [
    # 中文附录: 附录A, 附录B.1
    r'^(附录[A-Z]+(?:[\.．][\d]+)*)\s+(.+)',
    # 字母+数字编号: D.4, D.4.1, A.1, TB.10423 (字母后跟数字，可选点分隔)
    r'^([A-Z]+(?:[\.．]?\d+)+)\s+(.+)',
    # 纯数字编号: 1, 3.1, 1.0.1, 5.0.3
    r'^(\d+(?:[\.．]\d+)*)\s+(.+)',
]

# 层级上限（与旧实现一致的封顶，避免异常输入产生超深层级）
_MAX_LEVEL = 6


def _level_from_clause_no(clause_no: str) -> int:
    """层级唯一来源：编号的点数。整份文档只有这一把尺子。

    R1（182 号第三十二~四十一条）/ GB/T 1.1：层级由编号的段数唯一决定，
    与 Markdown 的 `#` 数量无关——PaddleOCR-VL 的 `#` 数量不稳定，双尺子会
    让 `### 18.3`（旧=3）与 `18.3.1`（旧=3）撞层，导致栈被提前 pop。

    例：'5'→1, '5.1'→2, '5.1.1'→3, '附录A'→1, 'A.1.3'→3, '16.0.2'→3
    """
    return min(1 + clause_no.count('.'), _MAX_LEVEL)


def _is_zero_segment_node(clause_no: str) -> bool:
    """R3（182 号第三十五条）：章内不分节时，条编号中**对应节的编号**用 "0" 表示。

    因此 `X.0.Y` 里的 `0` 段表示「本章不分节」：它不构成一个层级节点，
    所以不存在 `3.0` 这个「节」，`3.0.1` 的父链直接是 [`3 章名`]。

    ⚠️ 判据只能看**末段**：末段为 `'0'`（如 `3.0`）才是节位占位、不当节点；
    `3.0.1` / `1.0.2` 的末段是条号，它们**本身是条，必须照常入库**。
    若写成「任意段为 0」，会把所有 `X.0.Y` 条文一起丢掉——实测 JGJ107
    39 条 / 44,353 字符（占其 content 80%）、CJJ2 44 条 / 12,796 字符。
    """
    parts = clause_no.split('.')
    return len(parts) > 1 and parts[-1] == '0'


# 裸露的 4 位年份（如封面页的 "2008"）不是条文号
_BARE_YEAR = re.compile(r'^(19|20)\d{2}$')

# 标题式编号行特征：编号后的文本较短且无句末标点，视为标题而非正文
_TITLE_END_PUNCT = ('。', '；', '：', '.', '！', '？')

# ═══════════════════════════════════════════
# 非条文黑名单（前言/目次/条文说明/用词说明等整块非条文）
# ═══════════════════════════════════════════

# 精确命中集合（title 或 clause_no 精确匹配即判定为非条文）
_NON_CLAUSE_EXACT_TITLES = {
    "前言", "目次", "Contents", "条文说明",
    "本规程用词说明", "本规范用词用语说明",
}

# 「直接过滤」类：导入时完全不生成条文（不进入数据库）
# 目次/Contents 属于此类；前言/条文说明/用词说明则「打标保留」
_NON_CLAUSE_FILTER_TITLES = {"目次", "Contents"}


def is_non_clause_title(title) -> bool:
    """判断标题（或条文号）是否为非条文块

    规则（精确命中 + 子串/前缀兜底，兼容命名不统一）：
    - 精确命中：{前言, 目次, Contents, 条文说明, 本规程用词说明, 本规范用词用语说明}
    - title 以「前言」开头（含「前言」即可覆盖）
    - title 含「条文说明」（兼容「3.0.2 条文说明…」这类标题）
    - title 以「本规程用词说明」或「本规范用词用语说明」开头
    """
    t = (title or "").strip()
    if not t:
        return False
    if t in _NON_CLAUSE_EXACT_TITLES:
        return True
    if t.startswith("前言"):
        return True
    if "条文说明" in t:
        return True
    if t.startswith("本规程用词说明") or t.startswith("本规范用词用语说明"):
        return True
    return False


def is_filter_non_clause_title(title) -> bool:
    """判断标题是否属「直接过滤」类非条文（目次/Contents，导入时不生成条文）"""
    return (title or "").strip() in _NON_CLAUSE_FILTER_TITLES


# ═══════════════════════════════════════════
# 封面/出版信息页脏数据判定（导入时直接丢弃）
# ═══════════════════════════════════════════

# 封面特征词：命中 ≥2 个才判定为封面，避免误伤正常条文
_COVER_KEYWORDS = [
    "中华人民共和国国家标准",
    "ICS",                 # 标准分类号（如 ICS 77.140.60）
    "中国标准出版社",
    "出版发行",
    "代替 GB",
    "代替 GB/T",
    "代替 JGJ",
    "第一版",
    "印刷",                 # 覆盖「第X次印刷」
    "定价",
    "书号",
    "版权专有",
]


def is_cover_clause(content) -> bool:
    """判定条文内容是否为规范封面/出版信息页脏数据

    规则：命中封面特征词 ≥2 个才判定为封面（组合判定，避免误伤）。
    正常条文单出现「实施」「发布」等词不在特征词列表内，不会被判定。
    """
    c = content or ""
    hits = sum(1 for kw in _COVER_KEYWORDS if kw in c)
    return hits >= 2


def _match_clause_line(line: str):
    """检测非 # 前缀的编号行。返回 (clause_no, tail) 或 None

    tail 为编号后的文本。是否作为标题由调用方按 _looks_like_title 判断。
    **层级不由本函数推断**，调用方统一用 `_level_from_clause_no`（唯一尺子）。

    排除：目录行（含 5 个以上连续点）、纯日期行、无中文行、
    裸阿拉伯数字编号行（如 `1 钢筋`／`6 焊缝外观质量…`，按 R7 是条内的「项」）。
    """
    s = line.strip()
    if not s:
        return None
    # 排除目录行：含 "........."（含全角点）或 "第 x 页"
    if re.search(r'[\.．]{5,}', s) or re.match(r'^第\s*\d+\s*页', s):
        return None
    # 排除纯日期行: 2020-04-23 发布 / 2003 年3 月21 日
    if re.match(r'^\d{4}-\d{2}-\d{2}', s) or re.match(r'^\d{2,4}\s*年', s):
        return None
    # 排除无中文字符的行（如纯英文标题）
    if not re.search(r'[一-鿿]', s):
        return None
    for pattern in _NUM_PATTERNS:
        m = re.match(pattern, s)
        if m:
            # 统一归一化全角点号（U+FF0E）为半角，保证后续层级推断/查询一致
            clause_no = m.group(1).replace('．', '.')
            tail = m.group(2).rstrip(' .…')
            if not tail:
                return None
            # 排除数值噪音：以 0 开头的数字编号（0.95 是正文数值，非条文号）
            if re.match(r'^0', clause_no):
                return None
            # 裸阿拉伯数字编号行（如 "1 混凝土结构…" / "6 焊缝外观质量…"）不是条文：
            # 按 R7，它们是次分组单元内部的「项」，归属其所在的条。旧实现用
            # _looks_like_title 按字数猜，导致同一份文档里长项成正文、短项成条文，
            # 判定不一致（实测库里同时存在裸 '1'…'22' 与未被识别的裸项）。
            #
            # ⚠ 判据必须是 `isdigit()` 而**不是** `'.' not in clause_no`：
            # `附录A` 同样没有点号，但它是合法的结构编号，必须继续作为候选行
            # （tests/test_md_parser.py::test_parse_appendix_clauses 断言
            #  `附录A 接头型式检验的加载制度` 出现在后代条文的 parent_path 中）。
            if clause_no.isdigit():
                return None
            return (clause_no, tail)
    return None


def _looks_like_title(tail: str) -> bool:
    """编号行后的文本是否为短标题（而非正文句子）

    标题特征：短（≤20 字符）、不含句末标点。
    长句（含被 PDF 换行截断的正文）一律视为正文型编号行，
    避免把截断的条文内容误判为标题。
    """
    t = tail.strip()
    if not t:
        return False
    if len(t) > 20:
        return False
    if t.endswith(_TITLE_END_PUNCT):
        return False
    return True


def parse_markdown(md_text: str) -> list[dict]:
    """解析 Markdown，按标题层级切割条文。

    规则：
    - # 视为规范标题，忽略
    - ## ~ ###### 视为章节/条文标题
    - 无 # 前缀的编号行（如 3.1、D.4、附录A）也视为标题（兼容纯文本/OCR 输出）
    - 标题后的正文归属该条文
    - 中间层级标题（无正文或仅有子标题）不生成条文，但作为标签路径继承
    - 子条文自动继承父标题的标签路径（parent_path）

    返回: [{
        "clause_no": "5.2.1",
        "title": "原材料",
        "content": "钢筋进场时...",
        "level": 4,
        "parent_path": ["混凝土分项工程", "钢筋"],
        "is_non_clause": False   # True 表示非条文块（前言/条文说明/用词说明等，保留但默认隐藏）
    }, ...]

    黑名单行为：
    - 目次/Contents → 直接过滤，不生成 clause
    - 前言/条文说明/本规程用词说明 → 保留进库，is_non_clause=True
    - 「条文说明」段的正文型编号行（如 3.0.1 条文内容）继承打标
    """
    if not md_text.strip():
        return []

    lines = md_text.split("\n")
    clauses = []
    stack = []
    current_content_lines = []
    title_stack = []
    # 非条文继承标志：最近的非空标题命中「条文说明」等保留类非条文时，
    # 后续正文型编号行生成的 clause 也标记 is_non_clause=True
    inherit_non_clause = False
    # 过滤段标志：目次/Contents 段内的一切内容一律丢弃，直到下一个真实标题
    discard_section = False

    for line in lines:
        m_hash = re.match(r"^(#{1,6})\s+(.+)$", line)
        m_num = None
        if not m_hash:
            m_num = _match_clause_line(line)

        if m_hash or m_num:
            # 如果之前有积累内容且有当前条文号，保存之（完全空条文不生成）
            if current_content_lines and stack:
                entry = stack[-1]
                content = "\n".join(current_content_lines).strip()
                if _should_emit_clause(entry["title"], content):
                    clauses.append({
                        "clause_no": entry["clause_no"],
                        "title": entry["title"],
                        "content": content,
                        "level": entry["level"],
                        "parent_path": list(entry["parent_path"]),
                        "is_non_clause": entry.get("is_non_clause", False),
                    })
                current_content_lines = []

            if m_hash:
                raw_title = m_hash.group(2).strip()
                clause_no = _extract_clause_no(raw_title)
                title = _clean_title(_extract_title(raw_title))
                # R3：节位为 0 的占位号不成节点（`3.0` 不入库、不进 title_stack；
                # `3.0.1` 末段非 0，照常入库）
                if clause_no is not None and _is_zero_segment_node(clause_no):
                    continue
                # 黑名单：目次/Contents 直接过滤（不生成条文，丢弃段内内容）。
                # 必须**先于**下文的中文检查——`Contents` 无中文，若被中文检查提前
                # continue，`discard_section` 永不置位，目录行会漏进后一条正文。
                if is_filter_non_clause_title(title):
                    current_content_lines = []
                    inherit_non_clause = False
                    discard_section = True
                    continue
                if clause_no is None:
                    # 无编号标题：只有「非条文块」（前言/条文说明等，R8/R8b 打标保留）
                    # 继续以标题本身作编号走黑名单链路（旧行为）；
                    # 其余（如 `### 某英文标题`）不当条文（R1 ⑦）。
                    if not is_non_clause_title(title):
                        continue
                    clause_no = title
                # 编号是裸露年份，或标题无中文 → 不当条文（与 _match_clause_line 判据一致）
                if _BARE_YEAR.match(clause_no):
                    continue
                if not re.search(r'[一-鿿]', raw_title):
                    continue
                level = _level_from_clause_no(clause_no)
                discard_section = False
                is_non = is_non_clause_title(title)
                inherit_non_clause = is_non
                while title_stack and title_stack[-1][0] >= level:
                    title_stack.pop()
                title_stack.append((level, title))
                parent_path = [t[1] for t in title_stack[:-1]]
                stack.append({
                    "level": level, "clause_no": clause_no, "title": title,
                    "parent_path": parent_path, "is_non_clause": is_non,
                })
            else:
                # m_num 在此分支必定非 None（满足 if m_hash or m_num）
                assert m_num is not None
                clause_no, tail = m_num
                level = _level_from_clause_no(clause_no)
                if _looks_like_title(tail):
                    # 标题型编号行：与 # 标题行为一致
                    title = _clean_title(tail)
                    # 黑名单：目次/Contents 直接过滤
                    if is_filter_non_clause_title(title):
                        current_content_lines = []
                        inherit_non_clause = False
                        discard_section = True
                        continue
                    discard_section = False
                    is_non = is_non_clause_title(title)
                    inherit_non_clause = is_non
                    while title_stack and title_stack[-1][0] >= level:
                        title_stack.pop()
                    title_stack.append((level, title))
                    parent_path = [t[1] for t in title_stack[:-1]]
                    stack.append({
                        "level": level, "clause_no": clause_no, "title": title,
                        "parent_path": parent_path, "is_non_clause": is_non,
                    })
                else:
                    # 正文型编号行：编号即条文号，编号后文本即正文首行
                    # 不进入 title_stack（不作为后续条文的父级）
                    if discard_section:
                        # 目次/Contents 段内编号行：直接丢弃
                        continue
                    while title_stack and title_stack[-1][0] >= level:
                        title_stack.pop()
                    parent_path = [t[1] for t in title_stack]
                    stack.append({
                        "level": level, "clause_no": clause_no, "title": "",
                        "parent_path": parent_path,
                        "is_non_clause": inherit_non_clause,
                    })
                    current_content_lines.append(tail)
        else:
            if line.strip() and not discard_section:
                current_content_lines.append(line)

    # 处理最后一条（完全空条文不生成）
    if current_content_lines and stack:
        entry = stack[-1]
        content = "\n".join(current_content_lines).strip()
        if _should_emit_clause(entry["title"], content):
            clauses.append({
                "clause_no": entry["clause_no"],
                "title": entry["title"],
                "content": content,
                "level": entry["level"],
                "parent_path": list(entry["parent_path"]),
                "is_non_clause": entry.get("is_non_clause", False),
            })

    return clauses


def _extract_title(raw_title: str) -> str:
    """从完整标题中提取纯标题文本，去掉条文编号前缀。
    如 '5.1.1 一般规定' -> '一般规定'；'D.4 疏浚、吹填工程' -> '疏浚、吹填工程'
    """
    for pattern in _NUM_PATTERNS:
        m = re.match(pattern, raw_title)
        if m:
            return m.group(2).rstrip(' .…')
    m = re.match(r"^[\d．.]+\s+(.+)$", raw_title)
    if m:
        return m.group(1)
    return raw_title


def _extract_clause_no(raw_title: str) -> str | None:
    """从标题中提取条文号；**匹配不到编号返回 None**（不再兜底返回整串标题）。

    R1 ⑦：旧实现兜底 `return title` 会把 `### 某英文标题` 变成 clause_no，
    导致英文标题成条文、以及 `Ⅰ 主控项目` 这类非条文号入库（实测 124 条伪条文号）。

    兼容全角点号（U+FF0E），提取后统一归一化为半角点号。
    """
    for pattern in _NUM_PATTERNS:
        m = re.match(pattern, raw_title)
        if m:
            return m.group(1).replace('．', '.')
    return None


def _clean_title(title: str) -> str:
    """清理标题中的多余空格。如 '总    则' -> '总则'"""
    return re.sub(r'\s{2,}', '', title).strip()


def _should_emit_clause(title, content) -> bool:
    """判定是否生成条文：仅当标题与正文均为空时跳过（完全空条文）

    有标题但正文为空（如「总则」只有标题）→ 保留（正常行为）；
    无标题但有正文 → 保留。
    """
    return bool((title or "").strip() or (content or "").strip())
