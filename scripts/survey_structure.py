"""结构勘察：批次验收的回归门禁。

产出 **12 项**指标（批一验收用，见 spec §6 与 CEO 评审记录 Section 6）：
⚠️ M15：上面这个数字**必须**与下方清单、与 `_survey` 的返回键**三者一致** —— 本批已因
   「写了五项却列六项」栽过一次（Task 11 又加了 5 个键）。故不再靠人工同步：
   `tests/test_md_parser.py::test_survey_docstring_lists_every_metric` 同时比对
   数字、清单项与返回键，任一变动而不同步即变红（用阿拉伯数字而非「十一」即为了可被该门禁读）。
  1. clause_count           条文数
  2. content_chars          全部 content 的字符总数（**含 PaddleOCR-VL 标记的原始口径**）
  3. content_chars_plain    同上，但经 `plain_text` 归一（**Task 9 守恒断言的基线口径**）
  4. fake_clause_no_count   不含数字的条文号个数（伪条文号）
  5. breadcrumb_coverage    **条文行**的 section_path 段数 == 应有祖先数（按 R3 剔除 0 段）的占比
  6. missing_sections       **条文行**被引用却找不到标题的节号（量目次对齐的残余缺口）
  7. duplicate_clause_no    出现 >1 次的条号 → 次数（Task 11 重复诊断）
  8. duplicate_rows         落在重复号上的行数（= 指标 7 的次数之和）
  9. duplicate_rows_is_non  指标 8 里 `is_non_clause=1` 的行数（拆分「合法/缺陷」用）
 10. duplicate_group_kinds  **组级**三分解（Task 11 fix round 1）：按组内 `is_non_clause`
                            的分布分类 → `{"designed": 119, "body_only": 2, "commentary_only": 2}`
 11. duplicate_group_kinds_members  指标 10 每一类里的**号身份**（fix round 2）：
                            `{"body_only": ["10.7.3", "17.5.1"], "commentary_only": ["2", "前言"], …}`
 12. degraded_heading_rows  形如结构标题却**未成候选行**的行数（2026-09-29 降级行自检，
                            判据见 `md_parser.find_degraded_heading_lines`）。
                            正例：JTG F80/1-2017 的 `#### 4.2.1`（只有编号、标题在下一行）
                            83 行。**它量的不是"有没有丢文本"，而是"标题有没有被折进上一条"**
                            —— 后者不报错、条文数只差几条，只能靠人工翻条文发现。

⚠️ 指标 7~11（Task 11）：CJJ2 实测 123 组 / 258 行，其中打标 135 行。**组级**分解（探针实测，无余项）：
**119 组** `designed` —— **设计性**的「正文 + 条文说明同号」（1 条正文 + N 条逐款解释，注释侧
已由 Task 13 打标；样例 `16.8.3` = L4177 正文 + L7120/7122/7124/7126/7128 五条逐款说明）；
**2 组** `body_only` —— **真重复**（`10.7.3`、`17.5.1`），两次都在正文，属缺陷；
**2 组** `commentary_only` —— 条文说明段内自重复（`前言`、`2`）。

⚠️ **为什么必须有指标 10（指标 9 是弱信号）**：指标 9 只数「打标行总数」，把 `designed`
（合法）与 `commentary_only`（段内自重复）混在同一个数字里——①↔③ 此消彼长时它会**纹丝不动**。
指标 10 是**组级**分类，能分辨「是哪一类组变了」。可失败性已实测：把 `前言` 组 1 行的
`is_non_clause` 翻为 False、同时把 `10.7.3` 组 1 行翻为 True（**仅 2 处 flag 翻转**），
指标 7/8/9 全部**不变**（123 / 258 / 135），而指标 10 由 `119/2/2` 变为 `121/1/1` → 断言变红。

⚠️ **为什么还要有指标 11（指标 10 仍是计数）**：指标 10 只说「`body_only` 有 2 组」，不说
**是哪两个号**。而本 Task 要写进验收报告的结论恰是**号身份**——「2 个真重复是 `10.7.3`/`17.5.1`，
由夹具 L3077/L4802 造成」。若某个 `body_only` 号被另一个新伪影号**替换**（计数不变），
只有指标 11 会变红；否则报告会**无声地继续宣称旧结论**。可失败性已实测：把 `10.7.3` 组
**两行**的 `clause_no` 一起改写成 `99.9.9`（纯换号，行数/打标数/组数全不变），
指标 7~10 **全部不变**（123 / 258 / 135 / 119/2/2），而指标 11 的 `body_only` 由
`['10.7.3','17.5.1']` 变为 `['17.5.1','99.9.9']` → 断言变红。
按本批范式（Task 10 冻结的是**具体字典** `{'6.1.1':'模板',…}` 而非「3 条标签」）：
**冻具体值，不冻计数**。默认只钉 `body_only`/`commentary_only` 两类（短集合）；
`designed` 有 119 个号，计数已足够，不逐号断言。

⚠️ 指标 7 的值只对**重复号集合**有意义，故 `main()` 里不逐项打印（123 项），只打印指标 10/11 的分解。

⚠️ **字符量必须标口径（本批已多次踩坑）**：② 的两条伪影节点合计
**raw 3134 字符（0.703% of raw 445906）/ plain 484 字符（0.333% of plain 145269）**；
其中 `10.7.3` 伪节点 raw 3119 / plain 469 —— raw 与 plain 差 6.5 倍，全部来自渲染载荷
（`表 11.5.6-1` 的 HTML/LaTeX 标记）在 `plain_text` 下被剥离。简报引用的「484 字符」是
**plain 口径**，与本脚本指标 3 同口径；本脚本指标 2 是 raw 口径。两者都对，**不可混比**。
成因的正确描述是「**错位节点 + 吞并正文**」：该伪节点把 `表 11.5.6-1` 的正文记到了第 11 章下。

⚠️ 指标 5/6 均**只统计条文行**（R-T13-4/R-T13-5）：非条文行的面包屑按 `_build_section_path`
的契约本应为空，把它们算进来会系统性虚增缺口——Task 8 记录的 47 个「缺失节」里
**44 个是条文说明段噪声**（注释行引用正文结构里本就不存在的节号）。指标 6 判断
「父级是否已存在」也**只用条文行**（R-T13-5 fix round 2）：用全量行会让段内注释行
把真缺口掩盖掉（实测少报 3 个）。修正后残余缺口为 6。

用法：python scripts/survey_structure.py <md路径> [--json]
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.text_clean import plain_text  # noqa: E402
from app.parser.md_parser import parse_markdown, find_degraded_heading_lines  # noqa: E402

# ── 格式/显示常量（M15：原先散落为内联字面量与魔法数字） ──────────────────────
# ⚠️ `PATH_SEP` 必须与**产出侧**逐字一致：`section_path` 由
#    `app/parser/md_parser.py::_build_section_path` 用同一字面量 `" > "` 拼出
#    —— 那是本常量的**第三份字面量**，位于本批冻结的解析器内（本批不动它）。
#    改这里的值必须同步改那里，否则本脚本的两个指标会把整条面包屑当成一段、
#    覆盖率与缺口集合同时静默失真。
PATH_SEP = " > "
COVERAGE_DECIMALS = 4   # breadcrumb_coverage 的小数位（门禁断言取到 3 位有效小数）
MISSING_PREVIEW = 20    # CLI 里 missing_sections 的预览条数（明细走 --json）


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


def _duplicate_group_flags(clauses: list[dict]) -> dict[str, list[bool]]:
    """重复号 → 组内各行的 `is_non_clause` 列表。

    **指标 7~11 的唯一数据源**：`_survey` 的重复号相关指标全部由本函数的返回值派生
    （键序 = 各号在文档里首次出现的顺序，与 `Counter` 的插入序一致）。
    ⚠️ 不得在别处再写一份等价的分组循环——本批 Global Constraints 明令「公共逻辑抽离为
    工具函数，禁止复制粘贴重复代码」；fix round 3 修的正是「两份相同分组逻辑并存、
    其一静默未被调用」的实况（复核 Finding 1）。
    """
    counts = Counter(c["clause_no"] for c in clauses)
    flags: dict[str, list[bool]] = {}
    for c in clauses:
        if counts[c["clause_no"]] > 1:
            flags.setdefault(c["clause_no"], []).append(bool(c["is_non_clause"]))
    return flags


def _kind_of(flags: list[bool]) -> str:
    """组级分类：既有正文又有注释 / 全在正文 / 全在注释（指标 10 的三键）。"""
    if all(flags):
        return "commentary_only"
    if any(flags):
        return "designed"
    return "body_only"


def survey_structure(md_text: str) -> dict:
    """唯一公开入口：解析 md 文本并产出全部指标（见模块 docstring）。"""
    metrics = _survey(parse_markdown(md_text))
    # 指标 12 的判据作用在**候选行**层面（解析后拿不到），故在本层补、且放在最后
    # ——M15 门禁按顺序比对「docstring 清单 ↔ 返回键」。
    metrics["degraded_heading_rows"] = len(find_degraded_heading_lines(md_text))
    return metrics


def _survey(clauses: list[dict]) -> dict:
    """指标计算（与解析解耦）。拆出它是为了让 `main()` 只解析一次仍能拿到重复号明细。"""
    # ⚠️ M16：`fake_clause_no_count` **不是「缺陷个数」** —— 判据（逐字来自 brief）是
    #    「条号里不含阿拉伯数字」，故它把**合法的附录编号**一并计入。
    #    实测 CJJ2 该值为 **6**，逐行**全部**是合法条号（本 Task 探针实测，无余项）：
    #      `中华人民共和国住房和城乡建设部 公告` / `关于发布行业标准《…》的公告`
    #      / `前言` ×2 / `本规范用词说明` —— 5 行是**合法的非条文块**（is_non_clause=1，
    #      R8/R8b 明令「打标保留」）；第 6 行 `附录A` 是**合法结构编号**，
    #      且它自己持有 raw 35,122 / plain 1,992 字符的表格（`附录A 验收表`）。
    #    即：**6 = 5 个合法非条文块 + 1 个合法附录号，缺陷数为 0**。
    #    后续 Task 不要把「残余 6」当作待清信号追 —— 要追的是**该值重新变大**
    #    （即某处又冒出不带数字的伪条号），而不是「归零」。
    #    ✅ **2026-09-28（Task 17 / 改动⑤）该值 6 → 7**：新增的那 1 项同样是**合法非条文块**
    #    ——文首孤儿文本块（CJJ2 的身份取自块首行 `UDC`）。故「该值变大」这条信号必须按
    #    **余项成分**读：7 = 5 个非条文块 + 1 个附录号 + 1 个孤儿块，**缺陷数仍为 0**。
    #    （孤儿块是 `is_non_clause=True`，被检索侧的 `clause_is_non = 0` 过滤，不进结果。）
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
        segs = [s for s in (c.get("section_path") or "").split(PATH_SEP) if s]
        if segs and len(segs) == _expected_ancestor_count(c["clause_no"]):
            complete += 1
    coverage = complete / len(real) if real else 0.0

    # 残余缺口：条文编号的上一级节点号（如 18.3.1 → 18.3）在祖先链里找不到。
    # ⚠ R3：`X.0.Y` 的上一级是 `X.0`，而它按设计**不存在**（0 段不构成节点）
    #   → 必须跳过，否则 `3.0` / `1.0` / `2.0` 会被系统性报成「缺失的节」，
    #   与覆盖率指标一样夸大残余缺口、误导 Task 3 的去留判断（D6.2）。
    # ⚠️ R-T13-5（裁定 2026-09-27；fix round 2 收口）：本指标**循环与 `present` 必须同口径**，
    #   都只取条文行。两半理由不同、方向相反，都必须这样取：
    #   （一）非条文行**不该被统计**：其面包屑按 `_build_section_path` 的契约**本应为空**，
    #        算进来会系统性**虚增**缺口——Task 8 记录的 47 个里 **44 个是条文说明段噪声**。
    #   （二）`present` 若用**全量**行则**掩盖真缺口**：段内注释行只要恰好带某个节号，
    #        该节就被当成「父级存在」→ 真缺口被吞。实测提供者都是**非条文行**：
    #        clause_no `'14'`（标题「钢 梁」）、`'17.5'`、`'18.8'`。
    #   实测（夹具 `tests/fixtures/cjj2_source.md`，Task 13 后）：
    #     循环=条文行 & present=**全量**（错）  → ['10.7', '11.5', '8']                     （3 项）
    #     循环=条文行 & present=**条文行**（对）→ ['10.7', '11.5', '14', '17.5', '18.8', '8']（6 项）
    #   → 全量口径**在「没问题」的方向上撒谎**（少报 3 个真缺口，且少报的正是 Task 14 要修的）。
    #     6 项与 R-T8-6 记录的「正文触发数 6」数值吻合（两条独立路径互证）。
    #   ⚠️ 这 6 项的成因**不是**「注释引用了不存在的节号」（该说法已实测证伪）：其触发行的
    #     面包屑被**伪 level-1 节点**污染——实测 `14.3`→`'3'`、`17.5.8`→`'3'`、`18.8.9`→`'3'`、
    #     `10.7.3`→`'5'`、`11.5.4`→`'5'`、`8.5`→`'2'`（被 OCR 加 `##` 的裸数字行成了它们的祖先），
    #     属 **Task 14（`#` 裸数字行的伪节点）** 的范围；该 Task 修好后本集合会收敛，届时由其收紧断言。
    present = {c["clause_no"] for c in real}
    missing: set[str] = set()
    for c in real:
        parts = c["clause_no"].split('.')
        if len(parts) < 2:
            continue
        parent_no = ".".join(parts[:-1])
        if parts[-2] == '0':          # R3：节位为 0 → 该级本就不存在
            continue
        if parent_no in present:
            continue
        if not any(f"{parent_no} " in s for s in (c.get("section_path") or "").split(PATH_SEP)):
            missing.add(parent_no)

    # Task 11 重复条文号诊断：按 `clause_no` 计数，只留出现 >1 次的号。
    # ⚠️ 指标 8/9**不区分**「正文 / 条文说明」——同号重复的**合法性**恰恰要靠
    # `is_non_clause` 拆分来判定，故一并输出指标 10/11 的**组级**分解（指标 9 是弱信号，
    # 会把 `designed` 与 `commentary_only` 混成一个数字，见模块 docstring）。
    # ⚠️ fix round 3（复核 Finding 1）：指标 7~11 **全部**由 `_duplicate_group_flags` 派生。
    #    此前这里是**内联重算**的一份逐字等价的 flags dict，而那个函数从未被调用
    #    （死代码 + 两份相同分组逻辑并存）。按「消除重复、单一来源」修：删内联、走函数。
    flags = _duplicate_group_flags(clauses)
    duplicate_clause_no = {no: len(v) for no, v in flags.items()}
    kinds = ("designed", "body_only", "commentary_only")
    duplicate_group_kinds = {
        kind: sum(1 for v in flags.values() if _kind_of(v) == kind) for kind in kinds
    }
    # 指标 11：每一类里的**号身份**（排序后的列表，便于 `--json` 与断言）。指标 10 只是
    # 计数——号被同类的新号替换时计数不变，只有本键能发现（见模块 docstring）。
    duplicate_group_kinds_members = {
        kind: sorted(no for no, v in flags.items() if _kind_of(v) == kind)
        for kind in kinds
    }

    return {
        "clause_count": len(clauses),
        "content_chars": sum(len(c["content"]) for c in clauses),
        # ⚠ SDD 实施前扫描 F2：Task 9 的守恒断言口径是 `plain_text` 归一后的字符数
        # （避免 PaddleOCR-VL 标记残留导致自然波动），而上面的 `content_chars` 是
        # **含标记的原始口径**——两者不可混用。故本脚本必须同时输出归一口径，
        # 供 Task 9 直接取基线值。
        "content_chars_plain": sum(len(plain_text(c["content"])) for c in clauses),
        "fake_clause_no_count": len(fake),
        "breadcrumb_coverage": round(coverage, COVERAGE_DECIMALS),
        "missing_sections": sorted(missing),
        "duplicate_clause_no": duplicate_clause_no,
        # 指标 8/9 同样由 flags 派生（= 各组的行数之和 / 其中的 True 数），不再单独扫一遍 clauses
        "duplicate_rows": sum(len(v) for v in flags.values()),
        "duplicate_rows_is_non": sum(1 for v in flags.values() for f in v if f),
        "duplicate_group_kinds": duplicate_group_kinds,
        "duplicate_group_kinds_members": duplicate_group_kinds_members,
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    if not args:
        print(__doc__)
        return 2
    md = Path(args[0]).read_text(encoding="utf-8")
    clauses = parse_markdown(md)          # 只解析一次
    stats = _survey(clauses)
    if "--json" in sys.argv:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    for k, v in stats.items():
        if k == "missing_sections":
            print(f"{k}: {len(v)} 个 -> {v[:MISSING_PREVIEW]}")
        elif k == "duplicate_clause_no":
            print(f"{k}: {len(v)} 个（明细见指标 10 的组级分解）")
        else:
            print(f"{k}: {v}")

    # 组级分解的三类各列出**号名**（Task 11 的交付物是这张分解，不是那三个数字）：
    #   designed        = 正文 1 条 + 条文说明 N 条 —— **设计性**（逐款解释同号），合法
    #   commentary_only = 条文说明段内自重复（段内子标题/块重名）
    #   body_only       = **真重复**（两次都在正文）—— 缺陷，须定位源行
    kinds = stats["duplicate_group_kinds"]
    members = stats["duplicate_group_kinds_members"]
    names = {"designed": "设计性（正文 + 条文说明同号）",
             "commentary_only": "条文说明段内自重复",
             "body_only": "真重复（两次都在正文）"}
    print("  [重复成因组级分解]")
    for key in ("designed", "commentary_only", "body_only"):
        tail = " —— 批一验收必答项" if key == "body_only" else ""
        print(f"    {names[key]}: {kinds[key]} 组 -> {members[key]}{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

