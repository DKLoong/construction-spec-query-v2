import re


def parse_markdown(md_text: str) -> list[dict]:
    """解析 Markdown，按标题层级切割条文。

    规则：
    - # 视为规范标题，忽略
    - ## ~ ###### 视为章节/条文标题
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
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            raw_title = m.group(2).strip()

            # 如果之前有积累内容且有当前条文号，保存之
            if current_content_lines and stack:
                entry = stack[-1]
                clauses.append({
                    "clause_no": _extract_clause_no(entry["raw_title"]),
                    "title": _extract_title(entry["raw_title"]),
                    "content": "\n".join(current_content_lines).strip(),
                    "level": entry["level"],
                    "parent_path": list(entry["parent_path"]),
                })
                current_content_lines = []

            # 维护 title_stack（存储纯标题文本，用于标签路径继承）
            clean_title = _extract_title(raw_title)
            while title_stack and title_stack[-1][0] >= level:
                title_stack.pop()
            title_stack.append((level, clean_title))
            parent_path = [t[1] for t in title_stack[:-1]]  # 不包含自身

            stack.append({
                "level": level,
                "raw_title": raw_title,
                "parent_path": parent_path,
            })
        else:
            if line.strip():
                current_content_lines.append(line)

    # 处理最后一条
    if current_content_lines and stack:
        entry = stack[-1]
        clauses.append({
            "clause_no": _extract_clause_no(entry["raw_title"]),
            "title": _extract_title(entry["raw_title"]),
            "content": "\n".join(current_content_lines).strip(),
            "level": entry["level"],
            "parent_path": list(entry["parent_path"]),
        })

    return clauses


def _extract_title(raw_title: str) -> str:
    """从完整标题中提取纯标题文本，去掉条文编号前缀。
    如 '5.1.1 一般规定' -> '一般规定'
    """
    m = re.match(r"^[\d.]+\s+(.+)$", raw_title)
    if m:
        return m.group(1)
    return raw_title


def _extract_clause_no(title: str) -> str:
    """从标题中提取条文号，如 '5.1.1 一般规定' -> '5.1.1'"""
    m = re.match(r"^([\d.]+)\s", title)
    if m:
        return m.group(1)
    m = re.match(r"^第[一二三四五六七八九十百千万\d]+[节章条]", title)
    if m:
        return title
    return title
