"""结构勘察：批次验收的回归门禁。

产出五项指标（批一验收用，见 spec §6 与 CEO 评审记录 Section 6）：
  1. clause_count           条文数
  2. content_chars          全部 content 的字符总数（**含 PaddleOCR-VL 标记的原始口径**）
  3. content_chars_plain    同上，但经 `plain_text` 归一（**Task 9 守恒断言的基线口径**）
  4. fake_clause_no_count   不含数字的条文号个数（伪条文号）
  5. breadcrumb_coverage    **条文行**的 section_path 段数 == 应有祖先数（按 R3 剔除 0 段）的占比
  6. missing_sections       被引用却找不到标题的节号（量目次对齐的残余缺口）

用法：python scripts/survey_structure.py <md路径> [--json]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.text_clean import plain_text  # noqa: E402
from app.parser.md_parser import parse_markdown  # noqa: E402


def _expected_ancestor_count(clause_no: str) -> int:
    """按 R3 计算该条文**应有**多少级祖先（即面包屑应有的段数）。

    `X.0.Y`（章内不分节）没有 `X.0` 这一级，故 `3.0.1` 应有 1 级祖先（`3`）。
    **不能**用 `level - 1`：`3.0.1` 的层级是 3，`level-1=2` ≠ 实际 1 级，
    会把所有 `X.0.Y` 条文系统性误判为「面包屑不完整」——实测这类条文占
    JGJ107 的 53%（39/73）、CJJ2 的 44 条，足以让覆盖率被大幅低估，
    进而错误地让 Task 3（目次对齐）看起来有必要（D6.2 正是用本指标决策）。
    """
    segs = clause_no.split('.')[:-1]          # 去掉末段（条号）
    return sum(1 for s in segs if s != '0')   # R3：0 段不构成节点


def survey_structure(md_text: str) -> dict:
    clauses = parse_markdown(md_text)
    fake = [c for c in clauses if not re.search(r'\d', c["clause_no"])]

    # ⚠️ R-T13-4：覆盖率**只对条文行**统计。`_build_section_path` 的 docstring 明文写
    # 「非条文块不进面包屑」，故**非条文行的面包屑按设计为空**，把它算作「不完整」
    # 测不到任何东西（且会随非条文块规模的任何变动而漂移）。段级规则（Task 13）
    # 一次新增 164 条非条文行后，若仍按全行统计，覆盖率会从 0.8541「跌」到 0.7951——
    # 那是口径错，不是回归；改为只统计条文行后为 0.8589 → 0.9781，与 D6.2 已记录的
    # 「缺口集中在条文说明段、正文侧 0.97~0.99」一致。
    real = [c for c in clauses if not c["is_non_clause"]]
    complete = 0
    for c in real:
        segs = [s for s in (c.get("section_path") or "").split(" > ") if s]
        if segs and len(segs) == _expected_ancestor_count(c["clause_no"]):
            complete += 1
    coverage = complete / len(real) if real else 0.0

    # 残余缺口：条文编号的上一级节点号（如 18.3.1 → 18.3）在祖先链里找不到。
    # ⚠ R3：`X.0.Y` 的上一级是 `X.0`，而它按设计**不存在**（0 段不构成节点）
    #   → 必须跳过，否则 `3.0` / `1.0` / `2.0` 会被系统性报成「缺失的节」，
    #   与覆盖率指标一样夸大残余缺口、误导 Task 3 的去留判断（D6.2）。
    present = {c["clause_no"] for c in clauses}
    missing: set[str] = set()
    for c in clauses:
        parts = c["clause_no"].split('.')
        if len(parts) < 2:
            continue
        parent_no = ".".join(parts[:-1])
        if parts[-2] == '0':          # R3：节位为 0 → 该级本就不存在
            continue
        if parent_no in present:
            continue
        if not any(f"{parent_no} " in s for s in (c.get("section_path") or "").split(" > ")):
            missing.add(parent_no)

    return {
        "clause_count": len(clauses),
        "content_chars": sum(len(c["content"]) for c in clauses),
        # ⚠ SDD 实施前扫描 F2：Task 9 的守恒断言口径是 `plain_text` 归一后的字符数
        # （避免 PaddleOCR-VL 标记残留导致自然波动），而上面的 `content_chars` 是
        # **含标记的原始口径**——两者不可混用。故本脚本必须同时输出归一口径，
        # 供 Task 9 直接取基线值。
        "content_chars_plain": sum(len(plain_text(c["content"])) for c in clauses),
        "fake_clause_no_count": len(fake),
        "breadcrumb_coverage": round(coverage, 4),
        "missing_sections": sorted(missing),
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    if not args:
        print(__doc__)
        return 2
    md = Path(args[0]).read_text(encoding="utf-8")
    stats = survey_structure(md)
    if "--json" in sys.argv:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        for k, v in stats.items():
            if k == "missing_sections":
                print(f"{k}: {len(v)} 个 -> {v[:20]}")
            else:
                print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
