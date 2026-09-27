"""守恒断言：解析改造不得让正文凭空消失。

背景（CEO 评审 CRITICAL-1）：旧实现下「不含数字的条文号」124 条持有
252,514 字符，占全部 content 的 56%。若改造让这些伪条文号不再出现，
而它们的正文没有回流到真实的条上，就是 56% 语料静默蒸发。

两层护栏（round 2 Finding 1 重设计）：
1. **逐条下界**（`PER_CLAUSE_MIN_PLAIN`）：冻结若干**规范正文重头**条文的 plain 字符下界。
   它是唯一拦得住「**重分配式丢失**」的仪器——clause X 静默丢 ~3,000 字符、clause Y
   增 ≥3,000 时，总量不变、总量下界照常通过，但 X 跌破自身下界 → 红（见
   `test_heavy_normative_clauses_keep_min_plain_chars`；重分配验证构造与 RED/GREEN 证据见
   `.superpowers/sdd/2026-09-27-batch1-parser-hierarchy/round2-fixes-report.md` Finding 1）。
2. **总量下界**（`BASELINE_PLAIN_CHARS`）：只作粗网（防「整块正文蒸发」这类灾难性丢失）。
   旧写法取**精确当前值**（141,770）→ 零余量，任何未来改动都撞线、被迫反复重拉基线
   （Finding 1 (a)）；现留 `_TOTAL_HEADROOM` 余量。

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
_TOTAL_HEADROOM = 5000

BASELINE_PLAIN_CHARS = _CURRENT_PLAIN_CHARS - _TOTAL_HEADROOM   # 136,770

# 冻结的**规范正文**逐条下界（plain_text 归一口径）。取若干已知重头条文
# （下界 = 当前实测 − 每条约 10% 余量：留未来小改动的呼吸空间，又足以拦下
# 「整条正文被搬走/丢失」级的重分配——验证构造见 round2-fixes-report.md Finding 1）。
# ⚠️ 冻结的是具体条文号 → 具体下界，不冻「条数」（防「计数不变、号被替换」的无声失真，
# 与本批 survey_structure 指标 11 同一范式）。
PER_CLAUSE_MIN_PLAIN = {
    "23.0.1": 1800,   # 实测 2,025
    "14.3.1": 1700,   # 实测 1,946
    "8.4.8":  1650,   # 实测 1,905
    "10.7.3": 1300,   # 实测 1,486
    "10.7.5": 1150,   # 实测 1,340
    "16.10.5": 1150,  # 实测 1,312
}


@pytest.fixture
def cjj2_md() -> str:
    return CJJ2_FIXTURE.read_text(encoding="utf-8")


def test_content_is_conserved(cjj2_md):
    """正文总量不得低于粗网下界（留有余量；逐条下界见 test_heavy_normative_clauses_keep_min_plain_chars）"""
    clauses = parse_markdown(cjj2_md)
    total = sum(len(plain_text(c["content"])) for c in clauses)
    assert BASELINE_PLAIN_CHARS > 0, "先由 Task 8 Step 5 填入基线"
    assert total >= BASELINE_PLAIN_CHARS, (
        f"正文总量从 {BASELINE_PLAIN_CHARS} 降到 {total}——"
        f"有内容随伪条文号一并丢失（CRITICAL-1）"
    )


def test_heavy_normative_clauses_keep_min_plain_chars(cjj2_md):
    """重头条文的逐条下界：拦「重分配式丢失」（总量不变、单条被搬空/丢失）。

    总量下界 `test_content_is_conserved` 看不到重分配——clause X 丢 ~3,000 字符、
    clause Y 增 ≥3,000 时总量纹丝不动（Finding 1 (b)）。本测试冻结重头条文的**逐条**下界：
    任一被冻结条文跌破其下界即红，无论总量是否守恒。
    """
    clauses = parse_markdown(cjj2_md)
    by_no: dict[str, int] = {}
    for c in clauses:
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
