"""守恒断言：解析改造不得让正文凭空消失。

背景（CEO 评审 CRITICAL-1）：旧实现下「不含数字的条文号」124 条持有
252,514 字符，占全部 content 的 56%。若改造让这些伪条文号不再出现，
而它们的正文没有回流到真实的条上，就是 56% 语料静默蒸发。

两层护栏（round 2 Finding 1 重设计；round 3 Low-1/Low-2 校正口径、披露真实覆盖）：
1. **逐条下界**（`PER_CLAUSE_MIN_PLAIN`，共 6 条）：冻结若干**规范正文重头**条文的 plain 字符下界。
   它是两层里唯一拦得住「**重分配式丢失**」的仪器——clause X 静默丢 ~1,900 字符、clause Y
   增 ≥1,900 时，总量不变、总量下界照常通过，但 X 跌破自身下界 → 红（见
   `test_heavy_normative_clauses_keep_min_plain_chars`；重分配验证构造与 RED/GREEN 证据见
   `.superpowers/sdd/2026-09-27-batch1-parser-hierarchy/round2-fixes-report.md` Finding 1，
   round 3 复核见同目录 round3-report.md「验证 2」）。
   ⚠️ round 3 Low-1：聚合必须**排除 `is_non_clause` 行**（`not c["is_non_clause"]`）。
   否则同号的**条文说明**行会被折进「规范正文下界」——`23.0.1` / `8.4.8` 各另有一条
   `is_non_clause=1`、同 `clause_no` 的条文说明（plain 146 / 157），按 clause_no 汇总会
   得到 2,171 / 2,062，与本表「只冻结规范正文」的口径自相矛盾。
2. **总量下界**（`BASELINE_PLAIN_CHARS`）：只作粗网（防「整块正文蒸发」这类灾难性丢失）。
   旧写法取**精确当前值**（141,770）→ 零余量，任何未来改动都撞线、被迫反复重拉基线
   （Finding 1 (a)）；现留 `_TOTAL_HEADROOM` 余量。

⚠️ **本护栏的真实覆盖范围（round 3 Low-2 披露；下列数字均由本文件代码口径现场算出，可复现）**
——不要高估，它保证的是「**没有大额丢失**」，不是「正文无损」：
  - **冻结条文 6 条**，其规范正文合计 **10,014** plain 字符。
  - 该 10,014 占总量 **141,770 的 7.06%**（占规范正文 124,040 的 8.07%）。
    即 **92.9% 的正文没有任何逐条保护**。
  - 总量下界余量 **5,000**（`BASELINE_PLAIN_CHARS = 141,770 − 5,000 = 136,770`）。
  - 所以以下两类丢失**两层护栏都抓不到**，只能靠人工抽查 / 库级比对发现：
    (a) **只在非冻结条文之间发生的重分配**：例如把 1,200 字符从 `18.8.7`（1,305）
        搬到 `20.8.3`（1,302）——两条都不在冻结表内，总量不变 → 两层皆绿。
        （极端例：把最大的非冻结条文 `附录A`（1,992）整条搬空或删除，总量降到
        139,778 ≥ 136,770，**仍然全绿**。）
    (b) **缓慢渗漏**：总丢失 ≤ 5,000，且任一冻结条文的丢失 ≤ 其自身余量
        （6 条余量分别为 225 / 246 / 255 / 186 / 190 / 162，合计 **1,264**，
        全部落在总量 5,000 窗口之内）→ 两层皆绿。
  - 等价地：本护栏的保证是「**总量丢失 > 5,000** 或 **任一冻结条文丢失 > 其自身余量**」
    必定变红。(a)(b) 是其已知盲区，见 round3-report.md。

⚠️ 口径统一为 `plain_text()` 归一后的正文长度（与向量/FTS 消费方一致），避免
PaddleOCR-VL 的 HTML/LaTeX 标记残留导致自然波动。

⚠️ **逐条下界只冻结规范正文**（normative）；非规范块不入表，例外口径：
  - fix ④ 合法删除的**目录点引行**（TOC dot-leader，161 行 / plain 3,167）原在 `前言`
    （非规范块、is_non_clause=1）的 content 里，本就不在冻结表内；
  - 前言/条文说明/公告/用词说明/引用标准名录/页标记等非条文块一律不入表。
  故无任何「合法删除的正文」会误触发逐条下界。
"""
from pathlib import Path

import pytest

from app.ai.text_clean import plain_text
from app.parser.md_parser import parse_markdown

CJJ2_FIXTURE = Path(__file__).parent / "fixtures" / "cjj2_source.md"

# 当前实测总量（plain_text 归一口径，2026-09-27 fix/parser-three-narrowings 后）。
# 批次前基线 144,300；本批 fix ④ 合法删除 TOC 点引行 3,167 字符（fix ③ 反而 +22），
# 收敛为 141,770。见 three-narrowings-report.md §守恒核查。
_CURRENT_PLAIN_CHARS = 141770

# 总量下界余量：只作粗网，留出未来「合法删除非规范正文」的呼吸空间（fix ④ 已删 3,167），
# 避免零余量导致任何未来改动都撞线（Finding 1 (a)）。逐条下界才是拦重分配的主力。
# round 3 Low-2 给出这个数的出处，不再当魔数：5,000 ≈ 1.6 × 本批唯一一次合法删除量
# （fix ④ TOC 点引行 3,167 → 5000/3167 = 1.58），即「再发生一次同量级的合法删除仍不撞线」。
# 代价写进模块 docstring「真实覆盖范围」：盲区 (a)(b) 全部落在这个 5,000 窗口内。
# （对照量：最大的**非冻结**条文 `附录A` = 1,992 plain 字符，整条蒸发也仍在此窗口内。）
_TOTAL_HEADROOM = 5_000

BASELINE_PLAIN_CHARS = _CURRENT_PLAIN_CHARS - _TOTAL_HEADROOM   # 136,770

# 冻结的**规范正文**逐条下界（plain_text 归一口径）。取若干已知重头条文。
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
# 下界 = 实测 × ~0.9（六条实际余量 11.1% / 12.6% / 13.4% / 12.5% / 14.2% / 12.3%）：留未来
#    小改动的呼吸空间，又足以拦下「整条正文被搬走/丢失」级的重分配
#    （验证构造见 round2-fixes-report.md Finding 1）。
# ⚠️ 冻结的是具体条文号 → 具体下界，不冻「条数」（防「计数不变、号被替换」的无声失真，
#    与本批 survey_structure 指标 11 同一范式）。
# ⚠️ 覆盖范围有限（round 3 Low-2）：本表 6 条 / 10,014 plain 字符 = 总量的 7.06%，
#    非冻结条文之间的重分配抓不到 —— 详见模块 docstring「真实覆盖范围」。
PER_CLAUSE_MIN_PLAIN = {
    "23.0.1": 1800,   # 实测 2,025（规范正文；同号条文说明 146 已按 not is_non_clause 排除）
    "14.3.1": 1700,   # 实测 1,946（单行，fixture 里无同号条文说明）
    "8.4.8":  1650,   # 实测 1,905（规范正文；同号条文说明 157 已按 not is_non_clause 排除）
    "10.7.3": 1300,   # 实测 1,486（单行）
    "10.7.5": 1150,   # 实测 1,340（单行）
    "16.10.5": 1150,  # 实测 1,312（单行）
}


@pytest.fixture
def cjj2_md() -> str:
    return CJJ2_FIXTURE.read_text(encoding="utf-8")


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
    ⚠️ round 3 Low-2：本表只覆盖 6 条 / 10,014 plain 字符（总量 141,770 的 7.06%）——
    **非冻结条文之间的重分配抓不到**，详见模块 docstring「真实覆盖范围」。
    """
    clauses = parse_markdown(cjj2_md)
    by_no: dict[str, int] = {}
    for c in clauses:
        if c["is_non_clause"]:
            continue                      # 条文说明等非规范块不计入（round 3 Low-1）
        by_no[c["clause_no"]] = by_no.get(c["clause_no"], 0) + len(plain_text(c["content"]))
    for no, floor in PER_CLAUSE_MIN_PLAIN.items():
        got = by_no.get(no, 0)
        assert got >= floor, (
            f"条文 {no} 的 plain 正文 {got} 跌破下界 {floor}——"
            f"重分配式丢失未被总量下界拦住（CRITICAL-1）"
        )


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
