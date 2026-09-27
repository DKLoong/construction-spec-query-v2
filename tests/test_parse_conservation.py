"""守恒断言：解析改造不得让正文凭空消失。

背景（CEO 评审 CRITICAL-1）：旧实现下「不含数字的条文号」124 条持有
252,514 字符，占全部 content 的 56%。若改造让这些伪条文号不再出现，
而它们的正文没有回流到真实的条上，就是 56% 语料静默蒸发。

**唯一能拦住它的断言**：content 总字符数不减少。

实现陷阱（必须遵守）：content 含着 PaddleOCR-VL 的 HTML/LaTeX 标记，
若解析过程对标记的保留程度发生变化，字符数会自然波动。故断言统一基于
`plain_text()` 归一后的正文长度（与向量/FTS 消费方口径一致）。
"""
from pathlib import Path

import pytest

from app.ai.text_clean import plain_text
from app.parser.md_parser import parse_markdown

CJJ2_FIXTURE = Path(__file__).parent / "fixtures" / "cjj2_source.md"

# 批次**前**的实测基线（旧实现，plain_text 归一口径）。
# ⚠️ 口径必须取「批次开始前的 main」，**不是**批次内的中间 commit：
#   实测同一条命令（CJJ2 源 + sum(len(plain_text(content)))）：
#     main 2011760（批次前）      → 144,300
#     c0b4278（Task 1/2/4 后）    → 144,363   ← 拿这个当基线会低估，两栈口径不同
#     Task 3 修后                 → 144,974
#   取法：`git show <批次前 commit>:app/parser/md_parser.py` 到临时模块执行同一条公式
#   （控制器已实测过该手法，见 ledger 的 R-CONS）。
BASELINE_PLAIN_CHARS = 144300


@pytest.fixture
def cjj2_md() -> str:
    return CJJ2_FIXTURE.read_text(encoding="utf-8")


def test_content_is_conserved(cjj2_md):
    """正文总量不得减少（允许微小增长：次分组单元标签等文本并入 content）"""
    clauses = parse_markdown(cjj2_md)
    total = sum(len(plain_text(c["content"])) for c in clauses)
    assert BASELINE_PLAIN_CHARS > 0, "先由 Task 8 Step 5 填入基线"
    assert total >= BASELINE_PLAIN_CHARS, (
        f"正文总量从 {BASELINE_PLAIN_CHARS} 降到 {total}——"
        f"有内容随伪条文号一并丢失（CRITICAL-1）"
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
