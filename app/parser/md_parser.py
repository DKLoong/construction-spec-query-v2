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

    ⚠️ M5（Task 3 复核 Minor #3）**静默前提：入参 `clause_no` 必须已归一化（半角点号）** ——
    本函数只按半角 `.` 切分，不做任何归一化。全角点号（U+FF0E）由**两个产出方**先行归一：
    `_extract_clause_no` 与 `_match_clause_line`（各自 `m.group(1).replace('．', '.')`），
    本模块内的两处调用点（`:347` 的 `#` 分支、`:356` 的编号行分支）**都取自它们**，故实际安全。
    绕过它们直接传全角串会**误判**：实测 `_is_zero_segment_node('3．0')` → `False`
    （把「节位占位」当成真节点），而传归一化后的 `'3.0'` → `True` ✓。
    """
    parts = clause_no.split('.')
    return len(parts) > 1 and parts[-1] == '0'


# 裸露的 4 位年份（如封面页的 "2008"）不是条文号
_BARE_YEAR = re.compile(r'^(19|20)\d{2}$')

# fix ④: 目录点引行（TOC dot-leader：≥5 个连续半角/全角点）。
# 它们从不是规范正文（只是目次的页码对齐），故既不作候选行（`_match_clause_line`
# 已据此排除）、也不得进 `pending`（fix ④ 补上这后一半）。
_DOT_LEADER = re.compile(r'[\.．]{5,}')

# fix ③: 纯数字条号（只对这类做跨章/重复一致性检查）。
# 附录编号（`A.1`）与字母编号是**另一命名空间**，不受「跨章越级回跳」约束。
_NUMERIC_CLAUSE_NO = re.compile(r'^\d+(?:\.\d+)*$')

# feature ②: 次分组单元标签（R7/182 号第三十三条）：主控项目 / 一般项目，
# 可带大写罗马数字前缀（`Ⅰ `）。某节点的自身正文**完全**由这类标签行组成时，
# 该节点不是可检索条文，打标 is_non_clause=1（保留但默认隐藏）——CJJ2 的
# `### 9.6 检验标准` 后紧跟 `#### 主控项目`（标签行非候选 → 折入 9.6 自身正文），
# 使 9.6 成为 content 仅「主控项目」4 字的空壳条文（实测 6 行：5.4/6.5/7.13/8.5/9.6/12.5）。
_SUBGROUP_LABELS = ("主控项目", "一般项目")
_ROMAN_NUMERAL_PREFIX = re.compile(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*')

# OCR 管线的页分隔标记（`## 第X页`）。它必须被 parse_markdown 保留为独立候选，
# 否则每页不再隔离——见 `_candidate_of` 里 (a) 的说明与 `ocr_clean.py:11-12` 的契约。
_PAGE_MARKER = re.compile(r'^第\s*\d+\s*页$')

# 标题式编号行特征：编号后的文本较短且无句末标点，视为标题而非正文
_TITLE_END_PUNCT = ('。', '；', '：', '.', '！', '？')

# ═══════════════════════════════════════════
# 非条文黑名单（前言/目次/条文说明/用词说明等整块非条文）
# ═══════════════════════════════════════════

# 精确命中集合（title 或 clause_no 精确匹配即判定为非条文）
# R8/R8b（182 号第六、七条）法定名称扩充，新增的三项一律「打标保留」
# （clause_is_non=1，**不整段丢弃**，与「目次」的直接过滤不同）：
#   - 公告           法定名称（其带前缀/限定语的变体由 `_LEGAL_NAME_SUFFIXES` 覆盖，
#                    故此处与后缀表对「公告」二字有意重叠）
#   - 引用标准名录    实测 spec20 的 `### 引用标准名录` 标题行与其列表被并入
#                    上一条 `本规程用词说明` 的 content
#   - 标准用词说明    法定名称本身；其变体见 `_LEGAL_NAME_SUFFIXES`
_NON_CLAUSE_EXACT_TITLES = {
    "前言", "目次", "Contents", "条文说明",
    "公告", "引用标准名录", "标准用词说明",
}

# 「直接过滤」类：导入时完全不生成条文（不进入数据库）
# 目次/Contents 属于此类；前言/条文说明/公告/引用标准名录/用词说明则「打标保留」
_NON_CLAUSE_FILTER_TITLES = {"目次", "Contents"}

# 法定名称的**后缀**匹配表（R8/R8b，182 号第六、七条）。
# 计划口径是「改为按法定名称匹配，**再兼容变体**」：法定名称在真实文档里几乎总带
# 前缀或限定语，只认精确值会整块漏判。按**结尾**匹配即可覆盖同一法定名称的全部变体：
#   - 用词说明 / 用词用语说明（法定名称《标准用词说明》）
#       标准用词说明（法定名称）/ 本规范用词说明 / 本规程用词说明 / 本规范用词用语说明
#       —— 实测 CJJ2(f543577f) 写的是「本规范用词说明」，旧实现只认「本规程用词说明」，
#          差「用语」两字即漏判，整个用词说明块被并进上一条 `附录A 验收表` 的 content。
#   - 公告（法定名称）
#       —— 实测 CJJ2 的两条真实标题是 `中华人民共和国住房和城乡建设部 公告`(:58) 与
#          `关于发布行业标准《城市桥梁工程施工与质量验收规范》的公告`(:62)，都带前缀/
#          限定语，只认精确值「公告」两者全漏，公告块（批准文号/批准正文/发布单位/
#          日期）整段丢弃。
# 用**结尾**（而非子串）匹配：避免误伤「用词要求」「公告发布要求」这类真实条文标题。
_LEGAL_NAME_SUFFIXES = ("用词说明", "用词用语说明", "公告")


# ═══════════════════════════════════════════
# 条文说明段（文档级）——Task 13，182 号对文档顺序的规定
# ═══════════════════════════════════════════

# 182 号（第六、七条）规定条文说明位于文档**末尾**（在附录、用词说明、引用标准名录之后），
# 故一旦出现该段的起始标记，其后至文末一律为非条文。
# 实测（夹具 tests/fixtures/cjj2_source.md）：标记在行 6836，其后 164 条全部打标，
# 段前 734 条仅 4 条被打标（两条公告 + 前言 + 本规范用词说明，均合法）→ **零假阳性**。
# 真实语料（data/spec_query.db spec_id=20）：该段为 id 1362~1526 共 165 行，
# 其中 138 行与正文**同号**（Task 11 那批重复条文号的主力），此前全部未打标。
_COMMENTARY_MARKER = "条文说明"

# 法定名称的**受控前缀**（与 `_LEGAL_NAME_SUFFIXES` 同一设计原则：法定名称在真实文档里
# 几乎总带前缀或限定语，只认精确值会整块漏判）。182 号正文写作「附：条文说明」，
# CJJ2 的 md 里则是无前缀的裸行 `条文说明`——两者都要认。
# ⚠️ 小心目次行：`附：条文说明 ..... 247`（夹具行 231）剥掉前缀后仍带点引号与页码 → 不命中 ✓
_COMMENTARY_PREFIXES = ("附：", "附:")


def _is_commentary_marker(line: str) -> bool:
    """该行（**整行**）是否为条文说明段的起始标记。

    必须是整行精确匹配（剥掉受控前缀后），**不能**复用 `is_non_clause_title`：
    后者另含三条非精确规则（以「前言」开头、含「条文说明」、以法定名称结尾），
    用作**整行**判据会把正文行误判为段标记——实测夹具行 6843（含「条文说明」的
    正文长句）会因此开启该段，把其后正文整段打标
    （`content_chars` 445,893 → 445,871、覆盖率 0.8541 → 0.8532）。
    """
    t = line.strip()
    for prefix in _COMMENTARY_PREFIXES:
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    return t == _COMMENTARY_MARKER


def is_non_clause_title(title) -> bool:
    """判断标题（或条文号）是否为非条文块

    规则（精确命中 + 受控的变体兜底）：
    - 精确命中：{前言, 目次, Contents, 条文说明, 公告, 引用标准名录, 标准用词说明}
    - title 以「前言」开头（含「前言」即可覆盖）
    - title 含「条文说明」（兼容「3.0.2 条文说明…」这类标题）
    - title 以法定名称结尾（`_LEGAL_NAME_SUFFIXES`：用词说明 / 用词用语说明 / 公告），
      覆盖 标准用词说明 / 本规范用词说明 / 本规程用词说明 / 本规范用词用语说明 /
      ××公告 等全部变体（尾随限定语的写法如「…的补充」不再命中，属判据由
      「前缀兜底」收窄为「法定名称结尾」的既定设计）
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
    if t.endswith(_LEGAL_NAME_SUFFIXES):
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
    if _DOT_LEADER.search(s) or re.match(r'^第\s*\d+\s*页', s):
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


def _is_hash_line(line: str) -> bool:
    """该行是否以 Markdown 井号标题前缀开头（`#`~`######` + 空白）。

    与 `_candidate_of` 的 `#` 分支正则同源：只判前缀、不判是否成候选行。
    """
    return re.match(r"^#{1,6}\s", line) is not None


class _ParseState:
    """一次解析的**单调/位置状态**（fix ①②③ 共用）。

    预扫（`_vote_title_mode`）与主循环**各建一份**、按文档顺序同步推进，
    故两者的候选行判据一致（批一设计不变量：预扫投票与主循环必须用同一判据）。
    ⚠️ **不可跨两遍共享同一份**——预扫先跑完会把状态推到文末，主循环再从零读会全错。
    """

    __slots__ = ("max_bare_chapter", "in_commentary", "seen_clause_nos")

    def __init__(self) -> None:
        self.max_bare_chapter = -1           # fix ①: body 内已见的最高裸数字章号
        self.in_commentary = False           # 条文说明段标记（fix ①③ 的豁免边界，单向）
        self.seen_clause_nos: set[str] = set()  # fix ③: body 内已出现的纯数字条号


def _candidate_of(line: str, state: _ParseState | None = None) -> tuple[int, str, str] | None:
    """识别候选行 → `(level, clause_no, tail)`；不是候选行返回 `None`。

    **预扫投票（`_vote_title_mode`）与正式解析必须共用本函数**：两者判据若
    不一致，投票结果会对不上正式解析的那一行，兄弟表决就失去意义。

    **无编号裸行不成候选行（既有设计，不是缺陷）**：候选行必须带编号或 `#`（R1 ⑦）。
    T13 实测过「让裸行成候选」的写法：它让 `条文说明` 自成一格却又因「标题型且无自身
    正文」被内节点判据丢弃（−5 字符），且对段内打标毫无帮助（两层根因见 `_is_commentary_marker`）。
    故条文说明段的裸标记行由主循环的**文档级段规则**处理，不经本函数（裁定 R-T13-2）。
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
        # `#` + **裸数字**行：只有其后文本不像正文句子时才算节点。
        # 成因：OCR 会给表格/款文本误加 `##` 前缀（`## 3 钢箱梁悬臂拼装允许偏差应符合表17.5.7-2的规定。`、
        # `## 2 预应力筋安装应符合下列要求：`，夹具共 6 行）。它们是 level 1 的伪节点 →
        # 弹空栈、吞掉其后正文（实测 6 行 / 3,464 字符），并把真条文的 section_path
        # 污染成该伪节点标题。**实测值 `'3'`**（修复前 `bf9b419` 上复测：`18.8.9`/`18.8.10`/
        # `18.8.11`/`17.5.8`/`14.3` 的 section_path 恰为 `'3'`）—— 成因是该伪节点被 R14
        # 兄弟表决判为**正文型** → `title=''` → `_build_section_path` 的 `f"{clause_no} {title}".strip()`
        # 只剩编号。即「一个编号为 3 的伪 level-1 祖先」。
        # ⚠️ **已知的行为增量（本 Task 只让它与非 `#` 路径对齐，未新增丢失类别）**：被收窄的行，
        #    其自身文本与中间正文**不是折进所属条，而是无处归属被丢弃** —— 触发条件：它**是首个
        #    候选行**，或其前不存在可归属的候选行（即「归属其所在的条」只在**存在**可归属候选行时成立）。
        #    缓解事实（三条）：① 非 `#` 路径（Task 4 的 `clause_no.isdigit()`）**本就是这个行为**——
        #    同一行不加 `#` 时修复前后完全一致（实测两边都是 0 条 / 0 字符），故此非新缺陷类；
        #    ② 在本语料上不触发（净 +13 字符）；③ 真实 OCR 输出带 `## 第X页` 页标记（保留为候选）
        #    → 栈通常非空。已按 R-T14-3 记入 TODOS（缓冲孤儿文本挂到下一候选行，属行为设计变更）。
        # ⚠️ 判据**只取「以句末标点结尾」一条**，不要用 `_looks_like_title`：后者另含 20 字长度门，
        #    会误伤长章名（`3 施工准备与临时设施（含施工便道、临时用电）`，全文 23 字）。
        #    ⚠️ 该长章名是**假设性构造**：夹具只有其短版 `3 施工准备`，全仓语料无此长版；且实测
        #    选定判据与被否决判据在本语料上**数值完全相同**（都是 −6 行、覆盖率 0.9986）—— 即
        #    **语料无法区分二者**。唯一护栏是合成用例
        #    `tests/test_md_parser.py::test_long_chapter_name_with_bare_number_is_still_a_node`
        #    （已实跑变异 M3：换成 `not _looks_like_title(...)` 时该用例变红，其余全绿）。
        # ⚠️ `_TITLE_END_PUNCT` 的 `.` 成员在此调用点**不可达**：走到这里必有 `clause_no.isdigit()`，
        #    即 `_extract_clause_no` 已由 `_NUM_PATTERNS` 命中，故 `_extract_title` 必走同一分支并
        #    `rstrip(' .…')` —— 尾随半角 `.` 早被剥掉（实测 `3 施工准备.` → `'施工准备'`）；
        #    全角 `．`（U+FF0E）不在元组里、也不在 rstrip 集里，故**未覆盖**（实测
        #    `3 施工准备．` → 保留）。**两者都偏向「保留为节点」**＝假拒的安全方向，与本判据的
        #    取舍一致，故不补。
        # ⚠️ 本判据必须写在 `#` 分支：非 `#` 路径的同类收窄在 `_match_clause_line`
        #    （Task 4 的 `clause_no.isdigit()`），那条**覆盖不到本条缺陷**。
        if clause_no.isdigit() and _extract_title(raw_title).strip().endswith(_TITLE_END_PUNCT):
            return None
        # fix ①: body 内 `#`+裸数字节点的章号必须**严格递增**。章号已见过的行不是节点
        # （其文本像普通非候选行一样流入当前条 content）。成因（JGJ107 实测，源行
        # `data/outputs/aa96b73a/aa96b73a.md:422`）：附录 A 里 `## 2 变形测量标距` 是
        # A.1.1 的**子项**、被 OCR 误加 `##`，成了 chapter 2 的重复节点（真章号 1..7，
        # `2` 在 `7` 之后再次出现）。
        # 判据**只取「章号严格递增」**：`n <= max` 即重复/回跳 → 拒；否则推进 max。
        # ⚠️ 只在 body 内生效（`in_commentary` 豁免）：条文说明段会合法地重新从 1 编章号。
        # ⚠️ 预扫与主循环各持一份 state、按文档顺序同步推进同一判据 → 两者一致（不变量）。
        if state is not None and clause_no.isdigit() and not state.in_commentary:
            n = int(clause_no)
            if n <= state.max_bare_chapter:
                return None
            state.max_bare_chapter = n
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


def _reject_cross_chapter(cand: tuple[int, str, str], state: _ParseState) -> bool:
    """fix ③ (TODOS T22): 交叉引用被 PDF 断行后，下半行以「条号形状 token」起头，
    被 `_candidate_of` 误认成候选行、自成一条条文。

    判据 = **同号重复**：该纯数字条号在 body 内**已出现过**，即不是本条文档里
    合法的新条文，而是上文交叉引用的下半行。三条夹具伪影都是本类：
      - L3077 `10.7.3 条第2款的规定。` —— 正文侧已有真 `10.7.3`；
      - L4802 `17.5.1 条和第 13.7.2 条规定。` —— 正文侧已有真 `17.5.1`；
      - L4926 `14.3 节有关规定…` —— 正文已有节标题 `### 14.3 检验标准`。
    命中则**不是节点**，其文本折入当前条 content（与普通非候选行同一路径）。

    ⚠️ 只在 body 内生效（`in_commentary` 豁免）：条文说明段会**合法地**复用正文
    条号（每条正文对应一条逐款解释，同号、is_non_clause=1），故段内不得判重复。
    附录/字母编号（`A.1`）是另一命名空间，豁免。
    ⚠️ **为何不用「父号 vs `_parent_key`」的跨章比对**：T22 建议的该判据在
    「节标题缺失」的合法形态上会误拒——最小夹具 `1.1 总则 → 1.0.1`（X.0.Y 的
    有效父号按 R3 是 `1`，而栈内是节 `1.1`）、`## 条文说明 → 3.0.1`（父号 `3`
    而栈内是非数字的 `条文说明`）都会被误判。同号重复是**三条伪影共有、且唯一
    无假阳性的共性**，故只取这一条。
    """
    if state.in_commentary:
        return False
    _level, clause_no, _tail = cand
    if not _NUMERIC_CLAUSE_NO.match(clause_no):
        return False
    return clause_no in state.seen_clause_nos


def _vote_title_mode(lines: list[str]) -> dict[tuple[int, str], bool]:
    """R14 兄弟多数表决（预扫，只读，不改任何状态）。

    按 `(层级, 父键)` 分组；父键只用「编号 + 层级」推导，与标题/正文判定无关，
    因此可在正式解析之前算准。组内多数决定该组是「带标题条」还是「无标题条」。

    平票（偶数条且恰好半数）**一律判「无标题」**：判「无标题」时该行文本进入
    自身 content，不会丢；若回退首元素且它像标题，则整组判标题型，组内无自身
    正文的那条会按内节点被丢弃（工程评审 SC-6）。

    ⚠️ 本函数自带一份 `_ParseState`，按文档顺序推进与主循环**同一套**判据
    （fix ① 的章号单调在 `_candidate_of` 内、fix ③ 的跨章/重复在本函数内），
    保证预扫投票的行集合与主循环的行集合**逐行一致**（批一设计不变量）。
    """
    state = _ParseState()
    rows: list[tuple[int, str, str]] = []
    stack: list[dict] = []
    for line in lines:
        if _is_commentary_marker(line):
            state.in_commentary = True
        cand = _candidate_of(line, state)
        if cand is None:
            continue
        level, clause_no, tail = cand
        if _reject_cross_chapter(cand, state):
            continue
        rows.append((level, _parent_key(stack, level), tail))
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        stack.append({"level": level, "clause_no": clause_no})
        state.seen_clause_nos.add(clause_no)

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
    - **R3**：条号末段为 `0`（如 `3.0`）是「本章不分节」的**节位占位**，不构成层级节点、
      不入库；`X.0.Y`（如 `3.0.1`）本身是条，照常入库（判据见 `_is_zero_segment_node`）
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
                                  #   —— 或自身正文全是次分组标签的空壳节点（feature ②）
    }, ...]

    黑名单行为：
    - 目次/Contents → 直接过滤，不生成 clause，段内内容一律丢弃
    - 前言/条文说明/公告/引用标准名录/用词说明（含「标准用词说明」「本规范用词说明」
      等全部变体）→ 保留进库，is_non_clause=True（R8/R8b，182 号第六、七条）
    - 「条文说明」段的正文型编号行（如 3.0.1 条文内容）继承打标
    - 条文说明段（整行 `条文说明` / `附：条文说明`）→ 自该行起**至文末**全部打标
      （文档级段规则，依据 182 号：条文说明位于文档末尾；见 `_is_commentary_marker`）
    - `## 第X页` 页分隔标记 → 保留为隐藏条文（is_non_clause=True），使每页正文互不污染
    """
    if not md_text.strip():
        return []

    lines = md_text.split("\n")
    title_mode = _vote_title_mode(lines)        # 改动①：R14 预扫投票

    state = _ParseState()          # fix ①②③：与预扫各持一份、同序推进同一判据
    clauses: list[dict] = []
    stack: list[dict] = []          # 标题栈；只放候选行，末位即「当前条」
    pending: list[str] = []         # 当前条的待落内容
    discard_section = False         # 目次段：段内一切丢弃
    inherit_non_clause = False      # 条文说明段：正文型行继承打标
    in_commentary = False           # 条文说明段（文档级）：单向、至文末，见裁定 R-T13-3

    def flush() -> None:
        """结算「当前条」= `stack[-1]`。

        改动② **无条件结算**：旧实现只在 `current_content_lines` 非空时才结算。
        ⚠️ **归因更正（Task 3 复核）**：`3.0.1` 获救**不是**这一改动的作用 —— 它是被
        改动①（R14 投票判它**正文型**）救的：正文型把 tail 推进 `pending`，于是无论
        条件还是无条件结算都会产出它。本改动的**唯一真实行为效果**是「`stack` 为空时
        （即首个候选行之前）丢弃 `pending`」＝封面/前引文字不再并入首条真条文
        （设计性的泄漏修复，旧实现把它们并进了伪条文号）。

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
                    # feature ②：自身正文全是次分组标签的空壳节点同样打标（保留但默认隐藏）。
                    # 不改 stack 内 entry 的 is_non_clause，故其子条的面包屑仍含该节名。
                    "is_non_clause": entry["is_non_clause"] or _is_subgroup_label_only_body(content),
                })
        pending = []

    for line in lines:
        # 段级规则的**触发点**：在 `_candidate_of` 之前——标记行本身不改其归属
        # （它不成为候选行，见 `_candidate_of` docstring），只开启延伸至文末的非条文段。
        if _is_commentary_marker(line):
            in_commentary = True
            state.in_commentary = True           # fix ①②③：与预扫同步推进豁免边界
        cand = _candidate_of(line, state)
        if cand is None:
            if not discard_section and line.strip():
                # 非候选行 → 当前条的内容。次分组单元的标题行也走这里，
                # 需剥掉 Markdown 井号前缀，避免标记混进正文。
                # fix ④：目录点引行（≥5 连续点）从不是规范正文，不进 pending
                # （CJJ2 夹具无「目次」标题 → discard_section 不触发，138 行点引行
                # 曾漏进 `前言` 的 content）。
                if not _DOT_LEADER.search(line):
                    pending.append(re.sub(r'^#{1,6}\s*', '', line))
            continue

        if _reject_cross_chapter(cand, state):   # fix ③：同号重复一致性
            # 拒绝的候选行当作普通内容行：其文本折入当前条 content（与上分支同一条路径）。
            if not discard_section and line.strip():
                if not _DOT_LEADER.search(line):
                    pending.append(re.sub(r'^#{1,6}\s*', '', line))
            continue

        flush()                                  # 新候选行到达 → 先结算上一条
        level, clause_no, tail = cand

        # 投票键缺失只可能出现在「目次/Contents 行被主循环筛掉、未入栈」之后；
        # 回退 False（判「无标题」）与平票规则同向：文本进 content，不丢内容。
        # 投票只用于「确认标题」：组内多数判带标题 **且** 该行自身也像标题才算标题。
        # 这一「与」只会**减少**标题型判定，方向恒为**保内容**：长句不会仅因组内多数
        # 变成标题、进而因无自身正文被判为内节点而整条丢弃（实测修掉 CJJ2 的
        # 627 字符 / 21 条丢失——条文说明章的 `13.5 <整句>` 与正文 `13.5 悬臂拼装`
        # 同组，组内多数把长句推成 title）。
        # `#` 前缀行**无条件**判标题型：恢复批一前的语义——旧实现的 `#` 分支直接
        # `title = _clean_title(_extract_title(raw_title))`，从不调用 `_looks_like_title`，
        # `#` 标题恒为 title。批一的「统一」把「投票 + `_looks_like_title`」应用到
        # **所有**候选行，于是长 `#` 标题（>20 字，如 `### 21.4 防冲刷结构（锥坡、护坡、
        # 护岸、海墁、导流坝）` 22 字）与句末标点结尾的 `#` 标题被降级为正文型
        # （title=''）：自身成为 title='' 的畸形条文，其子条文的 parent_path 出现空串、
        # section_path 丢失节名（实测 22 条 `#` 标题被降级，其中 `21.4` 的 4 条子条文
        # parent_path 含空串、section_path 只剩编号）。
        # 此处只对 `#` 行放宽；非 `#` 行维持既有「投票 + `_looks_like_title`」逻辑不变。
        is_titled = _is_hash_line(line) or (
            title_mode.get((level, _parent_key(stack, level)), False)
            and _looks_like_title(tail)
        )
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
        # ⚠️ `in_commentary` 与 `inherit_non_clause` **必须分开**（裁定 R-T13-3）：
        # 后者保持「标题型行以自身裁定为准并**重置基调**」的既有语义（否则 `## 1 总则`
        # 会把前言段的基调外溢到其后全部条文，`test_parse_qianyan_retained_and_marked` 守之）；
        # 而条文说明段恰恰需要**穿透同名章标题**——段内每个 `## N 章名` 与正文章标题
        # 同形且同为 level 1，会把 level 1 的 `条文说明` 弹出栈，故没有任何局部规则
        # 能区分二者（实测：连「非条文祖先」也无从判断）。依据是 182 号对文档顺序的规定：
        # 条文说明在末尾，故「段内」≡「其后至文末」。
        is_non = ((own_non if title else (own_non or inherit_non_clause))
                  or in_commentary)
        inherit_non_clause = is_non

        while stack and stack[-1]["level"] >= level:
            stack.pop()
        stack.append({
            "level": level, "clause_no": clause_no, "title": title,
            "is_non_clause": is_non,
        })
        state.seen_clause_nos.add(clause_no)     # fix ③：与预扫同步记录已见条号
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


def _is_subgroup_label_only_body(content: str) -> bool:
    """该节点的自身正文是否**完全**由次分组单元标签行组成（feature ②）。

    只对「整段**全是**标签」成立；标签**夹在**正文中间（如 14.3.1 的
    `主控项目`/`一般项目` 分组标记）不算——那些标签是有效的分组语义，
    必须原样保留（实测 CJJ2 约 119 处）。17 条有前言正文的节（如 15.4）
    也因含非标签正文而不命中。
    """
    body = (content or "").strip()
    if not body:
        return False
    for line in body.split("\n"):
        t = line.strip()
        if not t:
            continue
        t = _ROMAN_NUMERAL_PREFIX.sub("", t)
        if t not in _SUBGROUP_LABELS:
            return False
    return True


def _should_emit_clause(title, content) -> bool:
    """判定是否生成条文：仅当标题与正文均为空时跳过（完全空条文）

    有标题但正文为空（如「总则」只有标题）→ 保留（正常行为）；
    无标题但有正文 → 保留。
    """
    return bool((title or "").strip() or (content or "").strip())
