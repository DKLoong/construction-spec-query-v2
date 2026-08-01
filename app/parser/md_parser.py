import re


# 编号行前缀正则（按匹配优先级排列），每个元素为 (pattern, level_base)
# level_base 用于编号深度推断层级（点数量 + level_base）
_NUM_PATTERNS = [
    # 中文附录: 附录A, 附录B.1
    (r'^(附录[A-Z]+(?:\.[\d]+)*)\s+(.+)', 1),
    # 字母+数字编号: D.4, D.4.1, A.1, TB.10423 (字母后跟数字，可选点分隔)
    (r'^([A-Z]+(?:\.?\d+)+)\s+(.+)', 1),
    # 纯数字编号: 1, 3.1, 1.0.1, 5.0.3
    (r'^(\d+(?:\.\d+)*)\s+(.+)', 1),
]

# 标题式编号行特征：编号后的文本较短且无句末标点，视为标题而非正文
_TITLE_END_PUNCT = ('。', '；', '：', '.', '！', '？')


def _match_clause_line(line: str):
    """检测非 # 前缀的编号行。返回 (level, clause_no, tail) 或 None

    tail 为编号后的文本。是否作为标题由调用方按 _looks_like_title 判断。

    排除：目录行（含 5 个以上连续点）、纯日期行、无中文行。
    """
    s = line.strip()
    if not s:
        return None
    # 排除目录行：含 "........." 或 "第 x 页"
    if re.search(r'\.{5,}', s) or re.match(r'^第\s*\d+\s*页', s):
        return None
    # 排除纯日期行: 2020-04-23 发布 / 2003 年3 月21 日
    if re.match(r'^\d{4}-\d{2}-\d{2}', s) or re.match(r'^\d{2,4}\s*年', s):
        return None
    # 排除无中文字符的行（如纯英文标题）
    if not re.search(r'[一-鿿]', s):
        return None
    for pattern, level_base in _NUM_PATTERNS:
        m = re.match(pattern, s)
        if m:
            clause_no = m.group(1)
            tail = m.group(2).rstrip(' .…')
            if not tail:
                return None
            # 排除数值噪音：以 0 开头的数字编号（0.95 是正文数值，非条文号）
            if re.match(r'^0', clause_no):
                return None
            # 单数字编号行（无点，如列表项 "1 混凝土结构..."）：仅当尾随短标题才视为标题，
            # 否则视为正文列表项（作为当前条文的内容，不切分）
            if '.' not in clause_no and not _looks_like_title(tail):
                return None
            # 编号深度推断层级：点数量 + level_base
            depth = clause_no.count('.')
            level = min(level_base + depth, 6)
            return (level, clause_no, tail)
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
        "parent_path": ["混凝土分项工程", "钢筋"]
    }, ...]
    """
    if not md_text.strip():
        return []

    lines = md_text.split("\n")
    clauses = []
    stack = []
    current_content_lines = []
    title_stack = []

    for line in lines:
        m_hash = re.match(r"^(#{1,6})\s+(.+)$", line)
        m_num = None
        if not m_hash:
            m_num = _match_clause_line(line)

        if m_hash or m_num:
            # 如果之前有积累内容且有当前条文号，保存之
            if current_content_lines and stack:
                entry = stack[-1]
                clauses.append({
                    "clause_no": entry["clause_no"],
                    "title": entry["title"],
                    "content": "\n".join(current_content_lines).strip(),
                    "level": entry["level"],
                    "parent_path": list(entry["parent_path"]),
                })
                current_content_lines = []

            if m_hash:
                level = len(m_hash.group(1))
                raw_title = m_hash.group(2).strip()
                clause_no = _extract_clause_no(raw_title)
                title = _clean_title(_extract_title(raw_title))
                while title_stack and title_stack[-1][0] >= level:
                    title_stack.pop()
                title_stack.append((level, title))
                parent_path = [t[1] for t in title_stack[:-1]]
                stack.append({
                    "level": level, "clause_no": clause_no, "title": title,
                    "parent_path": parent_path,
                })
            else:
                # m_num 在此分支必定非 None（满足 if m_hash or m_num）
                assert m_num is not None
                level, clause_no, tail = m_num
                if _looks_like_title(tail):
                    # 标题型编号行：与 # 标题行为一致
                    title = _clean_title(tail)
                    while title_stack and title_stack[-1][0] >= level:
                        title_stack.pop()
                    title_stack.append((level, title))
                    parent_path = [t[1] for t in title_stack[:-1]]
                    stack.append({
                        "level": level, "clause_no": clause_no, "title": title,
                        "parent_path": parent_path,
                    })
                else:
                    # 正文型编号行：编号即条文号，编号后文本即正文首行
                    # 不进入 title_stack（不作为后续条文的父级）
                    while title_stack and title_stack[-1][0] >= level:
                        title_stack.pop()
                    parent_path = [t[1] for t in title_stack]
                    stack.append({
                        "level": level, "clause_no": clause_no, "title": "",
                        "parent_path": parent_path,
                    })
                    current_content_lines.append(tail)
        else:
            if line.strip():
                current_content_lines.append(line)

    # 处理最后一条
    if current_content_lines and stack:
        entry = stack[-1]
        clauses.append({
            "clause_no": entry["clause_no"],
            "title": entry["title"],
            "content": "\n".join(current_content_lines).strip(),
            "level": entry["level"],
            "parent_path": list(entry["parent_path"]),
        })

    return clauses


def _extract_title(raw_title: str) -> str:
    """从完整标题中提取纯标题文本，去掉条文编号前缀。
    如 '5.1.1 一般规定' -> '一般规定'；'D.4 疏浚、吹填工程' -> '疏浚、吹填工程'
    """
    for pattern, _ in _NUM_PATTERNS:
        m = re.match(pattern, raw_title)
        if m:
            return m.group(2).rstrip(' .…')
    m = re.match(r"^[\d.]+\s+(.+)$", raw_title)
    if m:
        return m.group(1)
    return raw_title


def _extract_clause_no(title: str) -> str:
    """从标题中提取条文号，如 '5.1.1 一般规定' -> '5.1.1'；'D.4 疏浚' -> 'D.4'"""
    for pattern, _ in _NUM_PATTERNS:
        m = re.match(pattern, title)
        if m:
            return m.group(1)
    m = re.match(r"^第[一二三四五六七八九十百千万\d]+[节章条]", title)
    if m:
        return title
    return title


def _clean_title(title: str) -> str:
    """清理标题中的多余空格。如 '总    则' -> '总则'"""
    return re.sub(r'\s{2,}', '', title).strip()
