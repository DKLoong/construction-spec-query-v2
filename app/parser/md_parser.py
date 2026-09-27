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

# OCR 管线的页分隔标记（`## 第X页`）。它必须被 parse_markdown 保留为独立候选，
# 否则每页不再隔离——见 `_candidate_of` 里 (a) 的说明与 `ocr_clean.py:11-12` 的契约。
_PAGE_MARKER = re.compile(r'^第\s*\d+\s*页$')

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
    裸阿拉伯数字编号行（如 `1 钢筋`／`6 焊缝外观质量…`，它是条内的「项」，见下）。
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
            # 裸阿拉伯数字编号行（如 "1 混凝土结构…" / "6 焊缝外观质量…"）不是条文，
            # 而是所属条内部的「项」，归属其所在的条。**成因是两处收窄的合力，
            # 不是 R7**：R7（182 号第三十三条）讲的是次分组单元用**大写罗马数字**
            # 编号（`Ⅰ 主控项目`），与裸阿拉伯数字项无关——旧注释误引 R7，此处订正。
            #   ① 层级只由编号点数推导、`_NUM_PATTERNS` 不再含层级兜底后，裸编号
            #      与条内的「项」同形（`1 总则` 与 `1 钢筋` 无法区分）；
            #   ② 本判据把裸阿拉伯数字项整体排除出候选行。
            # 旧实现用 _looks_like_title 按字数猜，导致同一份文档里长项成正文、短项成
            # 条文，判定不一致（实测库里同时存在裸 '1'…'22' 与未被识别的裸项）。
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


def _candidate_of(line: str) -> tuple[int, str, str] | None:
    """识别候选行 → `(level, clause_no, tail)`；不是候选行返回 `None`。

    **预扫投票（`_vote_title_mode`）与正式解析必须共用本函数**：两者判据若
    不一致，投票结果会对不上正式解析的那一行，兄弟表决就失去意义。
    """
    if not line.strip():
        return None
    m_hash = re.match(r"^(#{1,6})\s+(.+)$", line)
    if m_hash:
        raw_title = m_hash.group(2).strip()
        clause_no = _extract_clause_no(raw_title)
        if clause_no is None:
            # 无编号标题：以下三类保留为候选，其余（如英文标题）不当条文（R1 ⑦）。
            t = _clean_title(_extract_title(raw_title))
            # (a) 页分隔标记（`## 第X页`）：**必须保留为候选**，以维持「每页独立隔离」的契约
            # ——`app/parser/ocr_clean.py:11-12` 明文承诺「保留它才能让 parse_markdown
            # 为封面页生成独立条文，再由 import 侧的 is_cover_clause 丢弃」。
            # 若它被筛掉：封面/前引文字会并入**首条真条文**，而 `is_cover_clause()`
            # 可能对该首条返回 True（实测特征词 ≥2 即 True）→ `_filter_cover_clauses`
            # （`import_routes.py:381`）把首条真条文连同其正文一并丢弃。
            # （Task 1 复核 Important #2，探针复现；旧实现靠该标记自成一格来隔离。）
            # 注意它必须走**本分支**：Task 1 已删掉 `第…[节章条]` 兜底，故
            # `_extract_clause_no("第1页")` 返回 None。处置：保留为候选，并在主循环里
            # 标 is_non_clause=1（隐藏不检索），等价于旧行为「自成一格 → 被封面过滤丢弃」，
            # 但不再依赖封面特征词。
            if _PAGE_MARKER.match(t):
                return (1, t, t)
            # (b) 法定非条文块（前言/条文说明/公告/引用标准名录/用词说明…）
            return (1, t, t) if is_non_clause_title(t) else None
        if _BARE_YEAR.match(clause_no):
            return None
        title_txt = _clean_title(_extract_title(raw_title))
        # ⚠️ 本分支的判据顺序是**承重**的，四处顺序都不可随意调换：
        #   ① 目次/Contents（过滤类）必须先于「无中文」检查 —— `Contents` 是英文、
        #      无中文，若先做中文检查会把它筛掉，于是主循环里的 `is_filter_non_clause_title`
        #      永远不触发、`discard_section` 从未置位，目录行会泄漏进下一条正文
        #      （Task 1 复核 Important #1，已探针复现）。
        #   ② 非条文块（前言/条文说明/公告…）必须先于 0 段检查 —— 否则
        #      `### 1.0 条文说明` 这种「占位号 + 非条文标题」会被整条丢掉，
        #      而不是以 is_non_clause=1 保留（Task 2 复核 Minor #1）。
        #   ③ 0 段检查必须在 `#` 分支**存在** —— 否则 Task 3 删掉 Task 2 的脚手架行后，
        #      `### 3.0` 重新成为节点，`test_zero_segment_is_not_a_node` 在本 Task
        #      的检查点上失败（Task 2 复核 Minor #2，前瞻性缺陷）。
        #   ④ 无中文检查最后。
        if is_filter_non_clause_title(title_txt):
            return (_level_from_clause_no(clause_no), clause_no, title_txt)
        if is_non_clause_title(title_txt):
            return (_level_from_clause_no(clause_no), clause_no, title_txt)
        if _is_zero_segment_node(clause_no):
            return None
        if not re.search(r'[一-鿿]', raw_title):
            return None
        return (_level_from_clause_no(clause_no), clause_no, title_txt)
    m_num = _match_clause_line(line.strip())
    if not m_num:
        return None
    clause_no, tail = m_num                      # Task 4 之后的返回形状
    if _is_zero_segment_node(clause_no):         # R3：末段为 0 的节位占位
        return None
    return (_level_from_clause_no(clause_no), clause_no, tail)


def _parent_key(stack: list[dict], level: int) -> str:
    """父键 = 栈中层级**严格小于** `level` 的最深候选行的 `clause_no`。

    投票与正式解析**共用本函数**，保证分组键一致。
    """
    for entry in reversed(stack):
        if entry["level"] < level:
            return entry["clause_no"]
    return ""


def _vote_title_mode(lines: list[str]) -> dict[tuple[int, str], bool]:
    """R14 兄弟多数表决（预扫，只读，不改任何状态）。

    按 `(层级, 父键)` 分组；父键只用「编号 + 层级」推导，与标题/正文判定无关，
    因此可在正式解析之前算准。组内多数决定该组是「带标题条」还是「无标题条」。

    平票（偶数条且恰好半数）**一律判「无标题」**：判「无标题」时该行文本进入
    自身 content，不会丢；若回退首元素且它像标题，则整组判标题型，组内无自身
    正文的那条会按内节点被丢弃（工程评审 SC-6）。
    """
    rows: list[tuple[int, str, str]] = []
    stack: list[dict] = []
    for line in lines:
        cand = _candidate_of(line)
        if cand is None:
            continue
        level, clause_no, tail = cand
        rows.append((level, _parent_key(stack, level), tail))
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        stack.append({"level": level, "clause_no": clause_no})

    groups: dict[tuple[int, str], list[str]] = {}
    for level, parent_key, tail in rows:
        groups.setdefault((level, parent_key), []).append(tail)

    verdict: dict[tuple[int, str], bool] = {}
    for key, tails in groups.items():
        yes = sum(1 for t in tails if _looks_like_title(t))
        # 平票 → False（无标题，保内容）；否则严格的多数
        verdict[key] = (yes * 2 > len(tails)) if yes * 2 != len(tails) else False
    return verdict


def _build_section_path(ancestors: list[dict]) -> str:
    """由**祖先**（不含自身）构建面包屑快照：`"6 混凝土分项工程 > 6.1 模板"`。

    非条文块（前言/条文说明等）不进面包屑——它们不是结构层级。
    无祖先返回**空串**（不带尾随分隔符：详情弹窗与批二的 FTS `breadcrumb` 列
    都会直接使用该串）。
    """
    return " > ".join(
        f"{a['clause_no']} {a['title']}".strip()
        for a in ancestors if not a.get("is_non_clause")
    )


def parse_markdown(md_text: str) -> list[dict]:
    """解析 Markdown，按**编号层级**切割条文。

    规则：
    - 候选行由 `_candidate_of` 唯一认定（`#` 标题行与无 `#` 的编号行共用一套判据）
    - 层级只由编号点数推导（`_level_from_clause_no`，唯一尺子）；与 `#` 数量无关
    - 同层各条「有无标题」由 R14 兄弟多数表决统一裁定（`_vote_title_mode` 预扫）：
      带标题条的编号后文本进 title，无标题条的编号后文本进 content
    - **无条件结算**：每条候选行在被下一条候选行取代（或文本结束）时结算一次
    - **内节点判据**：无自身正文的候选行（章名/节名）只作祖先、不入库
    - 非候选行的文本归属当前条，构成其 content
    - 祖先链取栈中**不含自身**的部分：`parent_path`（标签路径）与 `section_path`（面包屑）

    返回: [{
        "clause_no": "5.2.1",
        "title": "原材料",
        "content": "钢筋进场时...",
        "level": 3,
        "parent_path": ["混凝土分项工程", "钢筋"],
        "section_path": "5 混凝土分项工程 > 5.2 钢筋",   # 面包屑，不含自身
        "is_non_clause": False   # True 表示非条文块（前言/条文说明/页标记等，保留但默认隐藏）
    }, ...]

    黑名单行为：
    - 目次/Contents → 直接过滤，不生成 clause，段内内容一律丢弃
    - 前言/条文说明/本规程用词说明 → 保留进库，is_non_clause=True
    - 「条文说明」段的正文型编号行（如 3.0.1 条文内容）继承打标
    - `## 第X页` 页分隔标记 → 保留为隐藏条文（is_non_clause=True），使每页正文互不污染
    """
    if not md_text.strip():
        return []

    lines = md_text.split("\n")
    title_mode = _vote_title_mode(lines)        # 改动①：R14 预扫投票

    clauses: list[dict] = []
    stack: list[dict] = []          # 标题栈；只放候选行，末位即「当前条」
    pending: list[str] = []         # 当前条的待落内容
    discard_section = False         # 目次段：段内一切丢弃
    inherit_non_clause = False      # 条文说明段：正文型行继承打标

    def flush() -> None:
        """结算「当前条」= `stack[-1]`。

        改动② **无条件结算**：旧实现只在 `current_content_lines` 非空时才结算，
        于是「标题型且无后续内容」的行从未被结算——实测 3.0.1 就是这样消失的。

        改动③ **内节点判据**：无自身正文者只作祖先、不入库。
          - 标题型且有后续内容 → 有自身正文 → 叶条文
          - 标题型且无后续内容 → 内节点（章名/节名）
          - 正文型（title 为空）→ 其 tail 就是自身正文 → 必然产出

        改动④ 祖先链取 `stack[:-1]`，**不含自身**（与旧实现一致）。
        """
        nonlocal pending
        if stack:
            entry = stack[-1]
            content = "\n".join(pending).strip()
            has_own_body = (entry["title"] == "") or bool(content)
            if has_own_body and _should_emit_clause(entry["title"], content):
                ancestors = stack[:-1]
                clauses.append({
                    "clause_no": entry["clause_no"],
                    "title": entry["title"],
                    "content": content,
                    "level": entry["level"],
                    "parent_path": [a["title"] for a in ancestors],
                    "section_path": _build_section_path(ancestors),
                    "is_non_clause": entry["is_non_clause"],
                })
        pending = []

    for line in lines:
        cand = _candidate_of(line)
        if cand is None:
            if not discard_section and line.strip():
                # 非候选行 → 当前条的内容。次分组单元的标题行也走这里，
                # 需剥掉 Markdown 井号前缀，避免标记混进正文。
                pending.append(re.sub(r'^#{1,6}\s*', '', line))
            continue

        flush()                                  # 新候选行到达 → 先结算上一条
        level, clause_no, tail = cand

        # 投票键缺失只可能出现在「目次/Contents 行被主循环筛掉、未入栈」之后；
        # 回退 False（判「无标题」）与平票规则同向：文本进 content，不丢内容。
        is_titled = title_mode.get((level, _parent_key(stack, level)), False)
        title = _clean_title(tail) if is_titled else ""

        if is_filter_non_clause_title(title or tail):
            discard_section = True               # 目次 / Contents：段内一切丢弃
            inherit_non_clause = False
            continue
        discard_section = False

        # 非条文打标。**标题型行以自身裁定为准，并重置本段基调**——`## 1 总则`
        # 必须把前言段留下的 True 拨回 False，否则打标会外溢到其后**全部**条文
        # （旧实现 `:283` 即此语义；`test_parse_qianyan_retained_and_marked` 守之，
        # 且提交前的字面写法实测把它打挂：1.0.1 的 is_non_clause 变成 True）。
        # **正文型行**（title 为空）才继承本段基调：条文说明段内的编号行据此打标。
        own_non = (is_non_clause_title(title or tail)
                   or bool(_PAGE_MARKER.match(title or tail)))  # 页分隔标记：隐藏但保留（见 _candidate_of）
        is_non = own_non if title else (own_non or inherit_non_clause)
        inherit_non_clause = is_non

        while stack and stack[-1]["level"] >= level:
            stack.pop()
        stack.append({
            "level": level, "clause_no": clause_no, "title": title,
            "is_non_clause": is_non,
        })
        if not title:
            pending.append(tail)                 # 正文型：编号后文本即正文首行

    flush()                                      # 收尾：结算最后一条
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
