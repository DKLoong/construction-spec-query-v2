"""守恒断言：解析改造不得让正文凭空消失。

背景（CEO 评审 CRITICAL-1）：旧实现下「不含数字的条文号」124 条持有
252,514 字符，占全部 content 的 56%。若改造让这些伪条文号不再出现，
而它们的正文没有回流到真实的条上，就是 56% 语料静默蒸发。

三层护栏（round 2 Finding 1 重设计；round 3 Low-1/Low-2 校正口径、披露真实覆盖；
Task 19 / T26 加固 ①「夹具-常量绑定」、加固 ②「逐条覆盖 6 条 → 12 条」）：
1. **夹具-常量绑定**（`FIXTURE_RAW_CHARS` + `_CURRENT_PLAIN_CHARS`，Task 19 新增）：
   夹具是**被冻结的输入**，下面两层的每一个数字都由它推导。夹具一变，那些数字就全部作废——
   而危险方向是**变大**：夹具变大 ⇒ 总量下限偏低 ⇒ 守卫**静默变弱**（变小则响亮报红 ✓）。
   故把夹具钉住（见 `test_fixture_and_baseline_are_bound`），并用例证明这条断言
   **真的会红**（`test_grown_fixture_makes_the_binding_go_red`：在变大的夹具上，
   旧的总量下界照常通过，而新绑定断言红）。
2. **逐条下界**（`PER_CLAUSE_MIN_PLAIN`，共 12 条）：冻结**规范正文重头**条文的 plain 字符下界。
   它是三层里唯一拦得住「**重分配式丢失**」的仪器——clause X 静默丢 ~1,900 字符、clause Y
   增 ≥1,900 时，总量不变、总量下界照常通过，但 X 跌破自身下界 → 红（见
   `test_heavy_normative_clauses_keep_min_plain_chars`；重分配验证构造与 RED/GREEN 证据见
   `.superpowers/sdd/2026-09-27-batch1-parser-hierarchy/round2-fixes-report.md` Finding 1，
   round 3 复核见同目录 round3-report.md「验证 2」；Task 19 的可证伪用例见
   `test_redistribution_between_formerly_unfrozen_clauses_is_caught`）。
   ⚠️ round 3 Low-1：聚合必须**排除 `is_non_clause` 行**（`not c["is_non_clause"]`）。
   否则同号的**条文说明**行会被折进「规范正文下界」——`23.0.1` / `8.4.8` 各另有一条
   `is_non_clause=1`、同 `clause_no` 的条文说明（plain 146 / 157），按 clause_no 汇总会
   得到 2,171 / 2,062，与本表「只冻结规范正文」的口径自相矛盾。
3. **总量下界**（`BASELINE_PLAIN_CHARS`）：只作粗网（防「整块正文蒸发」这类灾难性丢失）。
   旧写法取**精确当前值**（141,770）→ 零余量，任何未来改动都撞线、被迫反复重拉基线
   （Finding 1 (a)）；现留 `_TOTAL_HEADROOM` 余量。
   ⚠️ 与第 1 层的口径区别（别把两者看成矛盾）：第 1 层要求**精确相等**——它断言的是
   「常量是否仍是**现行值**」，留容差就等于给「静默变弱」留窗口；本层留 **5,000 余量** ——
   它断言的是「**允许丢多少**」，留余量是为了给将来的合法删除呼吸空间。

⚠️ **本护栏的真实覆盖范围（Task 19 现场复算；下列数字均由本文件代码口径现场算出，可复现）**
——不要高估，它保证的是「**没有大额丢失**」，不是「正文无损」：
  - **冻结条文 12 条**（选表规则：**全部规范正文 ≥ 1,000 plain 字符的条文**，Task 19 加固 ②），
    其规范正文合计 **18,063** plain 字符。
  - 该 18,063 占**总 plain 142,445**（全部 rows，含 `is_non_clause` 的非规范块）的 **12.68%**；
    占**规范正文 plain 124,040**（只数 `not is_non_clause`，即上条的聚合口径）的 **14.56%**。
    ⚠️ 两个分母口径不同，**引用时必须写明是哪一个**（本批已因「夹具级 ≠ 库级」
    「raw ≠ 导入管线」的混用吃过两次亏）。
  - 等价地，**没有任何逐条保护**的正文：总量口径 **87.32%**、规范正文口径 **85.44%**。
    （加固前：冻结 6 条 / 10,014 plain，两个口径分别为 7.06% / 8.07%。）
  - 总量下界余量 **5,000**（`BASELINE_PLAIN_CHARS = 142,445 − 5,000 = 137,445`）。
  - 所以以下两类丢失**三层护栏都抓不到**，只能靠人工抽查 / 库级比对发现：
    (a) **只在「小条文」之间发生的重分配**（被搬的两条都 < 1,000 plain、都不在冻结表内）：
        例如把 `6.3.4`（940，现行最大的**未冻结**条文）整条搬到 `22.4.2`（938）——总量不变
        → 三层皆绿。**本类不含量级上限**：搬移按构造**保持总量**，而总量层对分配型丢失
        全盲 ⇒ 在 ≥6 条小条文之间累计搬 > 5,000 也照样三层全逃（单次「单条」量级只受
        「被搬条文本身 < 1,000」所限，不受 5,000 所限）。5,000 窗口约束的是**丢失**，见 (b)。
    (b) **缓慢渗漏**：总丢失 ≤ 5,000，且任一冻结条文的丢失 ≤ 其自身余量
        （12 条余量按实测降序为 225 / 242 / 246 / 255 / 186 / 190 / 162 / 155 / 152 / 152 / 140 / 108，
        合计 **2,213**，全部落在总量 5,000 窗口之内）→ 三层皆绿。
  - 等价地：本护栏的保证是「**总量丢失 > 5,000**」或「**任一冻结条文丢失 > 其自身余量**」
    或「**夹具与常量不再匹配**」必定变红。(a)(b) 是其已知盲区，见 round3-report.md。
  - ⚠️ **加固 ② 的效果与残留（Task 19）**：原表 6 条 / 10,014 = 7.06%，其 docstring 点名的
    两个盲区例子（`附录A` 1,992 整条蒸发；`18.8.7` 1,305 ↔ `20.8.3` 1,302 互搬）**现已入表**：
    `附录A` 整条蒸发 ⇒ 总量 140,453 ≥ 137,445（**总量层仍绿**），但逐条层红 ✓。
    残留盲区的**单条量级从「无上限」降到 < 1,000**（= 现行最大的未冻结条文）。

⚠️ 口径统一为 `plain_text()` 归一后的正文长度（与向量/FTS 消费方一致），避免
PaddleOCR-VL 的 HTML/LaTeX 标记残留导致自然波动。

⚠️ **逐条下界只冻结规范正文**（normative）；非规范块不入表，例外口径：
  - fix ④ 合法删除的**目录点引行**（TOC dot-leader，161 行 / plain 3,167）原在 `前言`
    （非规范块、is_non_clause=1）的 content 里，本就不在冻结表内；
  - 前言/条文说明/公告/用词说明/引用标准名录/页标记等非条文块一律不入表。
  故无任何「合法删除的正文」会误触发逐条下界。
"""
import copy
from pathlib import Path

import pytest

from app.ai.text_clean import plain_text
from app.parser.md_parser import parse_markdown

CJJ2_FIXTURE = Path(__file__).parent / "fixtures" / "cjj2_source.md"

# ── 加固 ①（Task 19 / T26）：夹具-常量绑定 ─────────────────────────────
# 夹具是**被冻结的输入**：本文件下面每一个数字都由它推导。它的字符数一变，那些数字就
# 全部作废；其中最危险的是**变大**——总量下限会随之偏低，守卫**静默变弱**而无人察觉
# （变小则撞线、响亮报红 ✓，见 `test_content_is_conserved`）。故把夹具钉在这里，
# 让「夹具变了」永远响亮报错，而不是悄悄降低保护强度。
# 复现：len(Path("tests/fixtures/cjj2_source.md").read_text(encoding="utf-8"))
# （`read_text` 走 universal newlines，CRLF / LF 两种检出都归一为 `\n`，故本数与平台无关。）
FIXTURE_RAW_CHARS = 460_011

# 现行实测总量（口径：`plain_text()` 归一后的正文长度，**全部** rows，含 is_non_clause
# 的非规范块；与 `test_content_is_conserved` 同式）。
#
# ⚠️ 本处出现过**两个数**，先后关系如下（**以后者为准**，勿再手抄旧值——这正是本单元
#    要消灭的失效模式）：
#   ① **批次基线（改前值）141,770** —— 批一 `fix/parser-three-narrowings` 在 2026-09-27
#      实测并写下：批次前 144,300 → fix ④ 合法删除 TOC 点引行 3,167 字符（fix ③ 反而 +22）
#      → 收敛为 141,770。它是**改前值**，不是当前值；本文件此前记的就是它。
#   ② **现行实测（raw fixture）142,445** —— 批二 T17/T20（孤儿文本守恒，夹具级 +621 等）
#      之后实测；T17/T18 报告里的 `content_chars_plain 141,770 → 142,445` 即此。
#   两者的差 = **+675**：即「常量静默过期」**已经实际发生**，且方向正是危险的一侧——
#   旧总量下限 141,770 − 5,000 = 136,770，比真值对应的 137,445 **低 675**，而 docstring 里
#   的覆盖率数字（冻结 6 条 / 10,014 / 7.06%）也停在旧值上。
#   Task 19 把常量拉回真值（总量下限随之上移 675 = **收紧 675**，不是放松），并由
#   `FIXTURE_RAW_CHARS` 与 `test_fixture_and_baseline_are_bound` 钉住，使其不再静默过期。
_CURRENT_PLAIN_CHARS = 142_445        # 现行实测 raw fixture（改前值 141,770 见上）

# 总量下界余量：只作粗网，留出未来「合法删除非规范正文」的呼吸空间（fix ④ 已删 3,167），
# 避免零余量导致任何未来改动都撞线（Finding 1 (a)）。逐条下界才是拦重分配的主力。
# round 3 Low-2 给出这个数的出处，不再当魔数：5,000 ≈ 1.6 × 本批唯一一次合法删除量
# （fix ④ TOC 点引行 3,167 → 5000/3167 = 1.58），即「再发生一次同量级的合法删除仍不撞线」。
# 代价写进模块 docstring「真实覆盖范围」：盲区 (a)(b) 全部落在这个 5,000 窗口内。
_TOTAL_HEADROOM = 5_000

BASELINE_PLAIN_CHARS = _CURRENT_PLAIN_CHARS - _TOTAL_HEADROOM   # 137,445

# 冻结的**规范正文**逐条下界（plain_text 归一口径）。
# **选表规则（Task 19 加固 ②）**：全部规范正文 **≥ 1,000 plain 字符**的条文（现行 12 条）——
# 规则式选取而非随手挑，把 docstring 里点名的两个盲区例子（`附录A` / `18.8.7` ↔ `20.8.3`）
# 一并纳入保护，并把「未冻结条文之间的重分配」的单条量级从无上限压到 < 1,000。
#
# 每行「实测 N」的算法 —— 精确表达式（可复现，与下方
# `test_heavy_normative_clauses_keep_min_plain_chars` 的聚合循环同式）：
#     Σ len(plain_text(c["content"]))
#       for c in parse_markdown(Path("tests/fixtures/cjj2_source.md").read_text(encoding="utf-8"))
#       if c["clause_no"] == <本行条文号> and not c["is_non_clause"]
# ⚠️ `and not c["is_non_clause"]` 不可省（round 3 Low-1）：`23.0.1` / `8.4.8` 在 fixture 里
#    各**另有一条** `is_non_clause=1`、同 `clause_no` 的**条文说明**行（plain 146 / 157）。
#    按 clause_no 汇总而不排除，实测值会虚高成 2,171 / 2,062 —— 那是把说明文字算进
#    「规范正文下界」，与本表口径自相矛盾（旧注释写的 2,025 / 1,905 本身没错，错在代码没这么算）。
# 下界 = 实测 × 0.9 向下取整到 50（现行余量 10.2% ~ 14.2%）：留未来小改动的呼吸空间，
#    又足以拦下「整条正文被搬走/丢失」级的重分配
#    （验证构造见 round2-fixes-report.md Finding 1 与
#    `test_redistribution_between_formerly_unfrozen_clauses_is_caught`）。
# ⚠️ 最早那六条（`23.0.1` ~ `16.10.5`）在制定此式之前定下，保留原值（余量同量级）。
# ⚠️ 冻结的是具体条文号 → 具体下界，不冻「条数」（防「计数不变、号被替换」的无声失真，
#    与本批 survey_structure 指标 11 同一范式）。
PER_CLAUSE_MIN_PLAIN = {
    # ── 原表 6 条（批一 round 2 定，本单元**未改动**任何一行）──────────────
    "23.0.1": 1800,   # 实测 2,025（规范正文；同号条文说明 146 已按 not is_non_clause 排除）
    "14.3.1": 1700,   # 实测 1,946（单行，fixture 里无同号条文说明）
    "8.4.8":  1650,   # 实测 1,905（规范正文；同号条文说明 157 已按 not is_non_clause 排除）
    "10.7.3": 1300,   # 实测 1,486（单行）
    "10.7.5": 1150,   # 实测 1,340（单行）
    "16.10.5": 1150,  # 实测 1,312（单行）
    # ── 加固 ② 新增 6 条（Task 19）：≥ 1,000 一档的其余全部经文 ────────────
    "附录A":  1750,   # 实测 1,992（合法的结构编号；docstring 原举的「整条蒸发仍全绿」例子）
    "18.8.7": 1150,   # 实测 1,305（docstring 盲区 (a) 里「搬出方」的例子）
    "20.8.3": 1150,   # 实测 1,302（docstring 盲区 (a) 里「搬入方」的例子）
    "20.8.6": 1100,   # 实测 1,252（单行）
    "10.7.2": 1000,   # 实测 1,140（单行）
    "13.3.5":  950,   # 实测 1,058（单行）
}


def _parse_total(md_text: str) -> int:
    """全部 rows（含 `is_non_clause` 的非规范块）的 plain 字符总量。"""
    return sum(len(plain_text(c["content"])) for c in parse_markdown(md_text))


def _assert_fixture_frozen(md_text: str) -> None:
    """夹具指纹（加固 ①）：夹具**一个字符都不能变**。"""
    assert len(md_text) == FIXTURE_RAW_CHARS, (
        f"夹具已变：{len(md_text)} 字符（{len(md_text) - FIXTURE_RAW_CHARS:+d}）"
        f"≠ 冻结的 {FIXTURE_RAW_CHARS}——"
        f"本文件的 `_CURRENT_PLAIN_CHARS` / `PER_CLAUSE_MIN_PLAIN` / docstring 覆盖率数字"
        f"全部由该夹具推导，必须逐项重算（**变大的方向尤其危险**：总量下限会偏低、"
        f"守卫静默变弱）"
    )


def _assert_baseline_bound(md_text: str) -> None:
    """常量与夹具的双向绑定（加固 ①）：实测总量必须**恰好**等于 `_CURRENT_PLAIN_CHARS`。

    要求恰好相等（而非容差）是有意的：任何偏差都意味着夹具或解析器变了，而
    「变大使下限偏低」正是本层要消灭的方向 —— 留容差就等于给静默变弱留窗口。
    代价是合法变更会撞线，那属**有意摩擦**（T26 记录在案）：改夹具/解析器就得显式重设常量。
    """
    total = _parse_total(md_text)
    assert total == _CURRENT_PLAIN_CHARS, (
        f"实测总量 {total}（{total - _CURRENT_PLAIN_CHARS:+d}）"
        f"≠ `_CURRENT_PLAIN_CHARS` {_CURRENT_PLAIN_CHARS}——"
        f"夹具或解析器已变，总量下限（= 常量 − {_TOTAL_HEADROOM}）与 docstring 的"
        f"覆盖率数字随之失真；**变大方向**意味着下限偏低、守卫静默变弱。"
        f"请重算常量、`PER_CLAUSE_MIN_PLAIN` 与本文件 docstring 的覆盖率数字"
    )


def _normative_plain_by_no(clauses: list[dict]) -> dict[str, int]:
    """按条文号汇总**规范正文**（排除 `is_non_clause`）的 plain 字符数。"""
    by_no: dict[str, int] = {}
    for c in clauses:
        if c["is_non_clause"]:
            continue                      # 条文说明等非规范块不计入（round 3 Low-1）
        by_no[c["clause_no"]] = by_no.get(c["clause_no"], 0) + len(plain_text(c["content"]))
    return by_no


def _assert_per_clause_floors(by_no: dict[str, int]) -> None:
    """逐条下界（第 2 层）：任一冻结条文跌破其下界即 AssertionError。"""
    for no, floor in PER_CLAUSE_MIN_PLAIN.items():
        got = by_no.get(no, 0)
        assert got >= floor, (
            f"条文 {no} 的 plain 正文 {got} 跌破下界 {floor}——"
            f"重分配式丢失未被总量下界拦住（CRITICAL-1）"
        )


@pytest.fixture
def cjj2_md() -> str:
    return CJJ2_FIXTURE.read_text(encoding="utf-8")


def test_fixture_and_baseline_are_bound(cjj2_md):
    """加固 ①：夹具与常量互绑 —— 夹具一变（尤其**变大**）必须响亮报错，不得静默变弱。

    夹具**变小**原本就会响亮报红（总量撞线，见 `test_content_is_conserved`）；
    **变大**则相反：下限偏低、守卫静默变弱。本用例把两个方向都钉死：
      - `_assert_fixture_frozen`：夹具指纹（任何增删都红，与解析器行为无关）；
      - `_assert_baseline_bound`：实测总量 == 常量（夹具与解析器**任一方**变了都红）。
    「这条断言真的会红」由 `test_grown_fixture_makes_the_binding_go_red` 构造证明。
    """
    _assert_fixture_frozen(cjj2_md)
    _assert_baseline_bound(cjj2_md)


def test_grown_fixture_makes_the_binding_go_red(cjj2_md):
    """加固 ① 的**可证伪性**：构造「夹具变大」，证明新断言真的会红、而旧门禁仍绿。

    若不构造，就无法排除「新断言恒真」——那正是本批反复清除的缺陷类别。
    后半段（旧总量下界在变大的夹具上照常通过）是「静默变弱」的直接证据：
    多出来的正文**没有任何保护**，而守卫一声不响。
    """
    grown = cjj2_md + "\n\n" + "本条规定了补充要求。" * 40 + "\n"

    # ① 旧门禁（只有总量下界）在变大的夹具上**照常通过** —— 这就是要消灭的静默变弱
    assert _parse_total(grown) >= BASELINE_PLAIN_CHARS, (
        "构造失效：夹具变大后旧总量下界竟然红了，说明本场景测不到「静默变弱」"
    )
    # ② 新绑定必须红（两条断言各自独立可红：指纹不看解析、绑定看解析产物）
    with pytest.raises(AssertionError):
        _assert_fixture_frozen(grown)
    with pytest.raises(AssertionError):
        _assert_baseline_bound(grown)


def test_content_is_conserved(cjj2_md):
    """正文总量不得低于粗网下界（留有余量；逐条下界见 test_heavy_normative_clauses_keep_min_plain_chars）

    ⚠️ 口径是**全部** rows（含 `is_non_clause` 的非规范块），因为它的任务是防「整块
    正文蒸发」，不是精度仪器。它**看不到重分配**——见模块 docstring「真实覆盖范围」。
    """
    clauses = parse_markdown(cjj2_md)
    total = sum(len(plain_text(c["content"])) for c in clauses)
    assert BASELINE_PLAIN_CHARS > 0, "先由 Task 8 Step 5 填入基线"
    assert total >= BASELINE_PLAIN_CHARS, (
        f"正文总量从 {BASELINE_PLAIN_CHARS} 降到 {total}——"
        f"有内容随伪条文号一并丢失（CRITICAL-1）"
    )


def test_heavy_normative_clauses_keep_min_plain_chars(cjj2_md):
    """重头条文的逐条下界：拦「重分配式丢失」（总量不变、单条被搬空/丢失）。

    总量下界 `test_content_is_conserved` 看不到重分配——clause X 丢 ~1,900 字符、
    clause Y 增 ≥1,900 时总量纹丝不动（Finding 1 (b)）。本测试冻结重头条文的**逐条**下界：
    任一被冻结条文跌破其下界即红，无论总量是否守恒。

    ⚠️ round 3 Low-1：聚合**只取规范正文**（`not c["is_non_clause"]`），与
    `PER_CLAUSE_MIN_PLAIN` 上方注释里的「实测」表达式**同式**。不排除时，同号**条文说明**
    行会被折入（`23.0.1` +146 → 2,171、`8.4.8` +157 → 2,062），冻结值就不再是「规范正文下界」。
    ⚠️ Task 19 加固 ②：本表已由 6 条扩到 **12 条 / 18,063 plain 字符（总量 142,445 的 12.68%）**
    —— **被搬的两条都 < 1,000 plain 时仍抓不到**，详见模块 docstring「真实覆盖范围」。
    """
    _assert_per_clause_floors(_normative_plain_by_no(parse_markdown(cjj2_md)))


def test_redistribution_between_formerly_unfrozen_clauses_is_caught(cjj2_md):
    """加固 ② 的**可证伪性**：构造两条**原先未冻结**条文之间的重分配（总量不变），
    证明新增的逐条下界拦得住它 —— 否则「扩表」只是数字好看。

    场景取模块 docstring 盲区 (a) 里被点名的原例：`18.8.7`（1,305，搬出）→ `20.8.3`
    （1,302，搬入）。**两者在加固前都不在冻结表内**，旧门禁对它全绿（这正是该盲区）。
    构造在**门禁自己的输入层**（parsed rows）上做：重分配就是「文本从一条挪到另一条」，
    而门禁读的正是这份 rows，故此处构造与真实丢失形态同层、可精确保证总量不变。
    """
    clauses = parse_markdown(cjj2_md)
    before = _normative_plain_by_no(clauses)
    _assert_per_clause_floors(before)          # 先证原状是绿的，否则红灯无法归因到本场景

    moved = copy.deepcopy(clauses)
    src = next(c for c in moved if c["clause_no"] == "18.8.7" and not c["is_non_clause"])
    dst = next(c for c in moved if c["clause_no"] == "20.8.3" and not c["is_non_clause"])
    dst["content"] = dst["content"] + src["content"]
    src["content"] = ""

    total_before = sum(len(plain_text(c["content"])) for c in clauses)
    total_after = sum(len(plain_text(c["content"])) for c in moved)
    assert total_after == total_before, (
        f"本场景必须**总量不变**（{total_before} → {total_after}），否则测的是总量层而非逐条层"
    )
    assert total_after >= BASELINE_PLAIN_CHARS, "总量层在本场景上照常通过（它看不见重分配）"
    with pytest.raises(AssertionError):
        _assert_per_clause_floors(_normative_plain_by_no(moved))


def test_no_fake_clause_no_carries_bulk_content(cjj2_md):
    """伪条文号不得再持有大块**正文**（旧的 `一般项目` 单条 raw 9,795 / **plain 1,200** 字符）。

    ⚠️ **两件仪器的实际覆盖范围（Task 9 复核实测，控制器复现）——不要高估本检查**：
    - **正向指纹**（`一般项目`/`主控项目` 不得出现在 `clause_no` 里）才是**针对已知缺陷形态**的**锋利**仪器：
      旧解析器上它们确实存在 → 该断言在旧解析器上**必然红**，可证伪 ✓。
    - **本行数检查（> 2000 plain 字符）在旧解析器上触发 0 次**：实测旧解析器的 124 个伪条文号里
      plain 最大的依次是 `前言` 3,496 / `附录A` 1,992 / `主控项目` 1,265 / `一般项目` 1,236 / `一般项目` 1,200，
      **唯一超 2000 的 `前言` 又按下面的口径被排除**（`is_non_clause`）。
      故它是**一般形态**的粗网（防「某个伪条文号重新持有大块正文」），**不是**本缺陷的判别器。
    - 单位别混：docstring 里的 9,795 是**原始**口径（`len(content)`），本行检查用的是 **`plain_text` 归一**口径
      （`附录A`：raw 35,122 / plain 1,992）。

    **口径必须排除两类**（控制器实施前预检实测；Task 9 复核逐条测了必要性并更正了我的措辞）：
    - `is_non_clause` 行 —— `前言` 块合法持有 **3,496** 字符，它不是「伪条文号吞正文」。
      **这一条是「必须」**：不加它，`前言` 会触发数检查。
    - `附录X` —— 合法结构编号，只因不含阿拉伯数字被 `fake_clause_no_count` 计数（M16），
      `附录A 验收表` 合法持有 **1,992** 字符（表格，离 2000 阈值仅 8 字符）。
      ⚠️ **这一条是「防御」而非「必须」**（复核实测更正）：`附录A` 是 1,992 < 2000，**不加它也不会触发** ——
      我原先写的「否则本断言必然红」**说过头了**。保留它是因为余量只有 8 字符（任何解析改动都可能把它推过阈值，
      届时会假红），且它**不构成逃逸通道**：`一般项目`/`主控项目` 既非非条文块、也不以 `附录` 开头 ✓。

    故本测试针对 CRITICAL-1 的**真实形态**：既非非条文块、也非附录的伪条文号不得持有大块正文。
    另加一条正向断言（旧缺陷的直接指纹），比阈值更锋利：
    """
    import re
    clauses = parse_markdown(cjj2_md)
    fake_big = [c for c in clauses
                if not re.search(r'\d', c["clause_no"])
                and not c["is_non_clause"]
                and not c["clause_no"].startswith("附录")
                and len(plain_text(c["content"])) > 2_000]
    # ⚠ M17（Task 9 复核 Concern C3）：筛选用 `plain_text` 口径，文案也必须用**同一口径** ——
    #    原写法打印 `len(c["content"])`（raw），读者会在「超 2000 字符」旁边看到一个量纲不同的
    #    数字（raw 可比 plain 大数倍：`附录A` raw 35,122 / plain 1,992）。故两者都换成 plain，
    #    并在文案里写明口径。
    assert not fake_big, (
        "仍有伪条文号持有超 2000 字符正文（plain_text 归一口径）: "
        f"{[(c['clause_no'], len(plain_text(c['content']))) for c in fake_big]}"
    )
    nos = {c["clause_no"] for c in clauses}
    for leaked in ("一般项目", "主控项目"):
        assert leaked not in nos, f"次分组单元 {leaked} 仍在充当条文号（CRITICAL-1 未修）"
