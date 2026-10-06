"""块 ↔ 行区间的锚定与拼接（spec §10，计划 Task 3）—— **P0 的硬门禁**。

核心性质：**不编辑 ⇒ 逐字节相等**。它不是"要对拍证明的命题"，而是**结构上恒真**
（拼接对未编辑区间原样取回原行）。真正要守的是：**锚定覆盖率 + 游标可信度 + 前缀保留**。

⚠️ **设计纠正（TDD 发现）**：计划原稿要求「内容出现两次 ⇒ 判歧义、拒绝锚定」，
但它同时要求「两个同内容块按序出现 ⇒ 都能锚定」—— 两者在"游标后有 >1 候选即歧义"
的规则下**互相冲突**。按阅读顺序**贪心取首个候选本来就是对的**（顺序消解重复）；
真正要防的是**前一个块漏锚 ⇒ 游标不动 ⇒ 后继块锚进未认领区域**。
故：`repeat_candidates` 只作**诊断**，`stale_anchors` 才是**门禁**。
"""
import json
import os
from pathlib import Path

import pytest

from app.ocr.layout import anchor_blocks, extract_layout, splice

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "ocr_layout_golden.jsonl"
REF_DIR = REPO / "data" / "_ocr_reference"


def _require_ref() -> None:
    if (REF_DIR / "raw.jsonl").is_file() and (REF_DIR / "reference.md").is_file():
        return
    if os.environ.get("REQUIRE_LAYOUT_FIXTURE") == "1":
        pytest.fail("缺少真实夹具：P0 验收不得空过")
    pytest.skip("缺少真实夹具 data/_ocr_reference/{raw.jsonl,reference.md}")


def _blocks_of(p: Path) -> list[dict]:
    return extract_layout([json.loads(l) for l in
                           p.read_text(encoding="utf-8").split("\n") if l.strip()])["blocks"]


def _blk(content, gid, page=1, label="text"):
    return {"label": label, "content": content, "page": page,
            "bbox": [0, 0, 1, 1], "order": gid, "global_block_id": gid}


# ══ 拼接：核心不变量 ═══════════════════════════════════

def test_splice_with_no_edits_is_byte_identical():
    """★ 不编辑 ⇒ **逐字节相等**（本方案的地基，不是"对拍跑出来的"）"""
    base = "A\n\nB\n\nC"
    bl = [{**_blk("A", 0), "start_line": 0, "end_line": 1, "prefix": ""},
          {**_blk("B", 1), "start_line": 2, "end_line": 3, "prefix": ""},
          {**_blk("C", 2), "start_line": 4, "end_line": 5, "prefix": ""}]
    assert splice(base, bl, {}) == base


def test_splice_touches_only_the_edited_block():
    base = "A\n\nB\n\nC"
    bl = [{**_blk("A", 0), "start_line": 0, "end_line": 1, "prefix": ""},
          {**_blk("B", 1), "start_line": 2, "end_line": 3, "prefix": ""},
          {**_blk("C", 2), "start_line": 4, "end_line": 5, "prefix": ""}]
    assert splice(base, bl, {1: "B2"}) == "A\n\nB2\n\nC"
    assert splice(base, bl, {0: "A2", 2: "C2"}) == "A2\n\nB\n\nC2"


def test_splice_preserves_hash_prefix_when_editing_a_title():
    """★ 编辑标题块**必须保留原文的 `#` 前缀**。

    标题块锚到 `## 目次`，块内容是 `目次`。若 splice 直接写新行，输出会是裸行
    `新目次` —— 直接改变 `parse_markdown` 的**标题型判定**（`#` 前缀行无条件判标题型）。
    """
    base = "## 目次\n\n正文"
    bl = [{**_blk("目次", 0, label="paragraph_title"), "start_line": 0, "end_line": 1,
           "prefix": "## "},
          {**_blk("正文", 1), "start_line": 2, "end_line": 3, "prefix": ""}]
    assert splice(base, bl, {0: "新目次"}) == "## 新目次\n\n正文"


def test_splice_raises_on_overlapping_spans():
    """★ 区间重叠是**编程错误**，必须显式失败 —— 静默跳过 = 用户那次编辑被悄悄丢掉"""
    base = "A\n\nB"
    bl = [{**_blk("A", 0), "start_line": 0, "end_line": 3, "prefix": ""},
          {**_blk("B", 1), "start_line": 2, "end_line": 4, "prefix": ""}]
    with pytest.raises(ValueError):
        splice(base, bl, {})


def test_splice_ignores_unanchored_blocks():
    base = "A\n\nB"
    bl = [{**_blk("A", 0), "start_line": 0, "end_line": 1, "prefix": ""},
          {**_blk("", 1, label="image"), "start_line": None, "end_line": None, "prefix": ""},
          {**_blk("B", 2), "start_line": 2, "end_line": 3, "prefix": ""}]
    assert splice(base, bl, {}) == base
    assert splice(base, bl, {1: "不该出现"}) == base


def test_splice_multiline_replacement():
    base = "A\n\nB"
    bl = [{**_blk("A", 0), "start_line": 0, "end_line": 1, "prefix": ""},
          {**_blk("B", 1), "start_line": 2, "end_line": 3, "prefix": ""}]
    assert splice(base, bl, {1: "B1\nB2"}) == "A\n\nB1\nB2"


# ══ 锚定：三条归一化 ═══════════════════════════════════

def test_anchor_tolerates_blank_lines_between_block_lines():
    """★ **行间空行**（实测逼出的第三条归一化，两轮外部复核都没发现）。

    实测：`markdown.text` 把 `block_content` 的**每一行渲染成独立段落**：
        block_content: '甲\\n乙'   →   reference.md: '甲' / '' / '乙'
    只容忍「连续行」的旧规则 ⇒ 6 页子集 51/60、完整 57 页 **541/593**；补上后 **552/593**。
    """
    base = "甲\n\n乙\n\n丙"
    anchored, stats = anchor_blocks(base, [_blk("甲\n乙", 0)])
    assert stats["missed"] == 0
    assert anchored[0]["start_line"] == 0 and anchored[0]["end_line"] == 3
    assert splice(base, anchored, {}) == base


def test_anchor_tolerates_hash_prefix_and_trailing_whitespace():
    """另两条归一化：**首行允许原文多一个 `#` 前缀**（记进 `prefix`）+ **去行尾空白**"""
    base = "## 目次\n\n前言.....4    \n1 总则.....6    "
    bl = [_blk("目次", 0, label="paragraph_title"), _blk("前言.....4\n1 总则.....6", 1)]
    anchored, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 0
    assert anchored[0]["prefix"] == "## " and anchored[1]["prefix"] == ""
    assert splice(base, anchored, {}) == base


def test_anchor_tolerates_html_wrapper_on_first_line():
    """★ 第四条归一化：首行被 HTML 标签**整行包裹**（实测逼出，见 Task 3 Step 5）。

    markdown 把**居中图题**写成 `<div style="text-align: center;">图 8.0.2 (mm)</div>`，
    而 `block_content` 是裸文本。不剥则 **9 个 `figure_title` 全部锚不上**（图题在
    审查页不可编辑）。且**前后缀必须记进 span** —— 否则编辑图题会丢掉居中 div，
    图注变左对齐（正是审查页要修的那类问题）。
    """
    base = '<div style="text-align: center;">图 8.0.2 J 环示意图 (mm)</div>\n\n正文'
    anchored, stats = anchor_blocks(
        base, [_blk("图 8.0.2 J 环示意图 (mm)", 0, label="figure_title")])
    assert stats["missed"] == 0
    assert anchored[0]["prefix"] == '<div style="text-align: center;">'
    assert anchored[0]["suffix"] == "</div>"
    # 未编辑 ⇒ 逐字节相等；编辑后**前后缀必须还在**
    assert splice(base, anchored, {}) == base
    out = splice(base, anchored, {0: "图 8.0.3 新图注 (mm)"})
    assert out.startswith('<div style="text-align: center;">图 8.0.3 新图注 (mm)</div>')


def test_anchor_uses_first_candidate_in_reading_order():
    """★ 重复内容**不是**歧义：按阅读顺序取首个候选（顺序本身消解重复）。

    计划原稿要求这里"拒绝锚定"，但那会让「两个同内容块按序出现」也无法锚定 ——
    两者互相冲突。故 `repeat_candidates` 只作**诊断**，不作门禁。
    """
    base = "重复行\n\n中间\n\n重复行"
    anchored, stats = anchor_blocks(base, [_blk("重复行", 0)])
    assert anchored[0]["start_line"] == 0
    assert stats["repeat_candidates"] == 1        # 诊断：确实还有第二个候选
    assert stats["missed"] == 0


def test_anchor_resolves_repeats_by_order():
    """两个同内容块按序出现 ⇒ 分别锚到两个位置"""
    base = "重复\n\n重复"
    anchored, stats = anchor_blocks(base, [_blk("重复", 0), _blk("重复", 1)])
    assert stats["missed"] == 0
    assert (anchored[0]["start_line"], anchored[1]["start_line"]) == (0, 2)


def test_anchor_marks_stale_anchors_after_a_miss():
    """★ **真正该防的**：前一个块漏锚 ⇒ 游标不动 ⇒ 后继块可能锚进未认领区域。

    `stale_anchors` 统计「在游标不可信状态下完成的锚定」，它才是门禁指标
    （比 `ambiguous` 更准：只惩罚真实的错位风险，不惩罚合法的重复）。
    """
    base = "甲文\n\n中段\n\n乙文"
    bl = [_blk("根本不在文中的内容", 0),      # 漏锚 ⇒ 游标停在 0 且标记不可信
          _blk("乙文", 1)]                    # 这一锚定就发生在不可信状态下
    anchored, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 1
    assert stats["stale_anchors"] == 1
    assert anchored[1]["start_line"] == 4


def test_anchor_resets_stale_after_a_confirmed_anchor():
    """漏锚之后**第一个成功锚定**重新确立位置，其后的锚定不再算 stale"""
    base = "甲文\n\n中段\n\n乙文\n\n丙文"
    bl = [_blk("不存在", 0), _blk("乙文", 1), _blk("丙文", 2)]
    _, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 1
    assert stats["stale_anchors"] == 1        # 只有紧随其后那一个


def test_anchor_exempts_labels_not_in_markdown():
    """豁免类别**不算未命中**（实测：number 57 + formula_number 21 + image 9）"""
    base = "正文"
    bl = [_blk("正文", 0),
          _blk("", 1, label="image"),
          _blk("(7.0.5)", 2, label="formula_number"),
          _blk("第1页", 3, label="number")]
    _, stats = anchor_blocks(base, bl)
    assert stats["exempt"] == 3 and stats["missed"] == 0 and stats["anchorable"] == 1


def test_anchor_reports_residual_miss_without_raising():
    """残留未命中**不抛**，但必须**逐条可解释**地报出来"""
    base = "完全不相干"
    anchored, stats = anchor_blocks(base, [_blk("这里没有这块", 0)])
    assert stats["missed"] == 1
    assert stats["residual_miss"] and stats["residual_miss"][0][1] == "text"
    assert anchored[0]["start_line"] is None
    assert splice(base, anchored, {}) == base


def test_anchor_no_content_block_is_exempt_not_missed():
    """内容为空的非豁免标签也算豁免（不得计成未命中）"""
    _, stats = anchor_blocks("正文", [_blk("正文", 0), _blk("   ", 1)])
    assert stats["missed"] == 0 and stats["exempt"] == 1


# ══ 表格兜底：两种序列化之间按**格内文字签名**定位 ═══════════
#
# 实测（控制器 2026-10-05，真实产物 `e9915ba8`）：9 个 `table` 块 `start_line` **全是 None**
# —— 因为**两侧是同一张表的两种序列化**：
#   - md 侧：带 `border/style`、跨多行、`<img>` 各自独占一行（该任务里表占 166–172 行）
#   - 块侧：OCR 给的**去样式、压成一行**的 `content`
# 逐行比对（`_find_candidates`）必然全灭 ⇒ 表格既不能高亮、也没有写回路径。
# 故加一条**表格专用兜底**：按"格子里写着什么"（剥标签 + 反转义 + 去空白后取前几格）匹配。

_T_MD_1 = ("<table border=1 style='margin: auto; word-wrap: break-word;'>"
           "<tr><td style='text-align: center; word-wrap: break-word;'>序号</td>"
           "<td style='text-align: center; word-wrap: break-word;'>图 形</td>"
           "<td style='text-align: center; word-wrap: break-word;'>说 明</td></tr>\n"
           "<tr><td style='text-align: center; word-wrap: break-word;'>1</td>\n"
           "<td style='text-align: center; word-wrap: break-word;'>"
           "<img src=\"imgs/img_1.jpg\" alt=\"Image\" /></td>\n"
           "<td style='text-align: center; word-wrap: break-word;'>紧急避难设施</td></tr></table>")

_T_MD_2 = _T_MD_1.replace("紧急避难设施", "公交机关")      # 同构、签名相同、位置不同

_T_BLK = ('<table><tr><td>序号</td><td>图 形</td><td>说 明</td></tr>'
          '<tr><td>1</td><td><img src="imgs/img_1.jpg" alt="Image" /></td>'
          '<td>紧急避难设施</td></tr></table>')

_T_BLK_2 = _T_BLK.replace("紧急避难设施", "公交机关")


def test_anchor_locates_styled_multiline_table():
    """★ 兜底生效：带样式、跨多行的 md 表格 + 去样式单行的块 content ⇒ 能锚上。

    断言到**行为**：span 恰好罩住那段 `<table>…</table>`（首行以 `<table` 开头、
    末行含 `</table>`），不是只断言"不为 None"。
    """
    base = "正文一。\n\n" + _T_MD_1 + "\n\n正文二。"
    bl = [_blk("正文一。", 0), _blk(_T_BLK, 1, label="table"), _blk("正文二。", 2)]
    anchored, stats = anchor_blocks(base, bl)
    tb = anchored[1]
    assert tb["start_line"] is not None, "表格块仍未锚定 —— 兜底没生效"
    assert stats["missed"] == 0
    seg = base.split("\n")[tb["start_line"]:tb["end_line"]]
    assert seg[0].lstrip().startswith("<table"), f"span 起点不在 <table 行：{seg[0][:40]!r}"
    assert "</table>" in seg[-1], f"span 终点不含 </table>：{seg[-1][:40]!r}"


def test_anchor_resolves_repeated_tables_by_order():
    """★ 同构表格**各自锚到自己的区域** —— 否则编辑会写错位置。

    ⚠️ 控制器用朴素「首次命中」实测过：9 个块**全部指向第一处**（md 行 166）。
    故这里必须断言两个 span **不同**，且分别罩住各自的表。
    """
    base = _T_MD_1 + "\n\n中间段。\n\n" + _T_MD_2
    bl = [_blk(_T_BLK, 0, label="table"), _blk(_T_BLK_2, 1, label="table")]
    anchored, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 0
    s0, s1 = anchored[0]["start_line"], anchored[1]["start_line"]
    assert s0 is not None and s1 is not None and s0 != s1, \
        f"两张表锚到了同一处（{s0}/{s1}）—— 顺序消歧没生效"
    lines = base.split("\n")
    assert "紧急避难设施" in "\n".join(lines[anchored[0]["start_line"]:anchored[0]["end_line"]])
    assert "公交机关" in "\n".join(lines[anchored[1]["start_line"]:anchored[1]["end_line"]])


def test_anchor_does_not_guess_when_table_absent():
    """★ 兜底**不许猜**：md 里没有这张表 ⇒ 维持未锚定并计入 missed（本仓既有口径）。"""
    base = "正文里没有任何表格。"
    bl = [_blk("正文里没有任何表格。", 0), _blk(_T_BLK, 1, label="table")]
    anchored, stats = anchor_blocks(base, bl)
    assert anchored[1]["start_line"] is None, "md 里没有该表却锚上了 —— 这是猜"
    assert stats["missed"] == 1


def test_splice_replaces_exactly_the_table_region():
    """★ 写回正确性：对表格 span 做 splice，**只**替换那段 `<table>`。

    证明方式是把新表文替换回原文，结果与 base **逐字节相同** —— 这同时排除了
    「span 多吃了相邻行」与「少吃了表尾」两种错。
    """
    base = "正文一。\n\n" + _T_MD_1 + "\n\n正文二。"
    bl = [_blk("正文一。", 0), _blk(_T_BLK, 1, label="table"), _blk("正文二。", 2)]
    anchored, _ = anchor_blocks(base, bl)
    new = "<table><tr><td>改过</td></tr></table>"
    got = splice(base, anchored, {1: new})
    assert new in got and "正文一。" in got and "正文二。" in got
    assert got.replace(new, _T_MD_1) == base, "span 没有恰好罩住表格区域"


# ══ 真实夹具门禁 ═══════════════════════════════════════

MAX_MISS_RATE = 0.02      # 与 scripts/verify_layout_anchor.py 同一阈值


def test_anchor_real_fixture_gate():
    """★ **P0 门禁**：真实夹具上必须满足
      - 未编辑 ⇒ **逐字节相等**
      - 可锚定块 > 0
      - 残留未命中率 ≤ 2%，且**每条都能报出标签**（否则无从归因）

    ⚠️ `stale_anchors` 是**诊断**，**不是门禁**：无漏锚 ⇒ 无 stale，故它逻辑上
    蕴含于 `missed == 0`，是后者的更严重述而非独立信号（写成门禁会变成
    "换个方式要求 missed==0"，却看起来像多了一层保障）。

    **实测（2026-10-04，完整 57 页 / 563 可锚定）：未命中 2（0.36%）**，成因已诊断：
      · `table` ×1 —— markdown 版 `<table border=1 style='…'>` 与 `block_content`
        的 `<table><tr>…` **属性不同**
      · `chart` ×1 —— 表示**根本不同**：markdown 是 HTML 表格，`block_content` 是管道表
    两条都属「表示形态不同」，不是漏了归一化规则。
    """
    _require_ref()
    base = (REF_DIR / "reference.md").read_text(encoding="utf-8")
    anchored, stats = anchor_blocks(base, _blocks_of(REF_DIR / "raw.jsonl"))

    assert splice(base, anchored, {}) == base, "未编辑必须逐字节相等"
    assert stats["anchorable"] > 0, "可锚定块为 0 ⇒ 夹具或分类有问题"

    rate = stats["missed"] / stats["anchorable"]
    assert rate <= MAX_MISS_RATE, (
        f"残留未命中率 {rate:.2%} 超阈值 {MAX_MISS_RATE:.0%}：{stats['residual_miss'][:10]}")
    for _idx, label, head in stats["residual_miss"]:
        assert label, "残留必须报出标签，否则无从归因"
        assert head is not None

    print(f"\n锚定：可锚定 {stats['anchorable']} 已锚定 {stats['anchored']} "
          f"未命中 {stats['missed']}（{rate:.2%}）重复候选 {stats['repeat_candidates']} "
          f"stale锚定 {stats['stale_anchors']} 豁免 {stats['exempt']}")
    for _idx, label, head in stats["residual_miss"]:
        print(f"  残留：{label} {head!r}")


# ══ 表格块携带 md 原文（P3 表格编辑器的数据前提） ═══════════

def test_table_block_carries_md_html():
    """★ 表格块必须带上它在 md 里的**原文** —— 编辑与写回都以它为准。

    为什么不能拿块的 `content` 当编辑源：`content` 是 OCR 的**去样式版**，而
    `border=1` / `style='margin: auto'` 是表格边框与居中的**唯一来源**（渲染侧无补偿 CSS）。
    """
    base = "正文一。\n\n" + _T_MD_1 + "\n\n正文二。"
    bl = [_blk("正文一。", 0), _blk(_T_BLK, 1, label="table"), _blk("正文二。", 2)]
    anchored, _ = anchor_blocks(base, bl)
    tb = anchored[1]
    assert tb["start_line"] is not None, "表格块没锚上（前置不成立）"
    assert "md_html" in tb, "表格块没带 md_html"
    assert tb["md_html"] == _T_MD_1, "md_html 不等于 md 里那段原文"


def test_non_table_block_has_no_md_html():
    """★ md_html **只给表格块** —— 别的 label 带上它会让 payload 无谓膨胀。"""
    base = "正文一。\n\n正文二。"
    anchored, _ = anchor_blocks(base, [_blk("正文一。", 0), _blk("正文二。", 1)])
    assert all("md_html" not in b for b in anchored)


def test_unanchored_table_has_no_md_html():
    """★ 没锚上的表格块**不得**凭空带 md_html（没有 span 就没有"它在 md 里的原文"）。"""
    base = "正文里没有表格。"
    anchored, _ = anchor_blocks(base, [_blk("正文里没有表格。", 0), _blk(_T_BLK, 1, label="table")])
    assert anchored[1]["start_line"] is None
    assert "md_html" not in anchored[1]


_IMG_A = '<div style="text-align: center;"><img src="imgs/a.jpg" /></div>'
_IMG_B = '<div style="text-align: center;"><img src="imgs/b.jpg" /></div>'


def test_image_blocks_pair_to_loose_img_lines_by_order():
    """★ 纯图块按序锚到 md 里**表格之外**的 `<img>` 行。

    **为什么**：`image` 在 `not_in_markdown` 豁免名单里 ⇒ 主循环给它 `start_line=None`；
    但没有行区间**就推不出条文号**、左栏画不出框、审查页只能挂「原文件未定位」
    —— 用户无法判断这张图将来会落进哪一条。而 md 里这些 `<img>` 行是**真实存在**的
    （实测某 122 页文档：表格外 `<img>` 行 132 行 ↔ `image` 块 132 个，逐块核对
    「该行落在的条文」与「前后邻块的条文」**132/132 完全一致**）。

    ⚠️ 同时钉住 **`splice` 的无重叠不变量**：区间一旦相撞 `splice` 会抛 `ValueError`
    （那是"用户那次编辑被悄悄丢掉"的守门人）。不改任何块 ⇒ 必须逐字节相同。
    """
    md = "正文一。\n\n" + _IMG_A + "\n\n正文二。\n\n" + _IMG_B + "\n\n正文三。"
    bl = [_blk("正文一。", 0), _blk("", 1, label="image"),
          _blk("正文二。", 2), _blk("", 3, label="image"), _blk("正文三。", 4)]
    anchored, st = anchor_blocks(md, bl)
    lines = md.split("\n")
    a_line = lines.index(_IMG_A)
    b_line = lines.index(_IMG_B)
    assert st["image_paired"] == 2, f"没有按序配对：{st}"
    assert (anchored[1]["start_line"], anchored[1]["end_line"]) == (a_line, a_line + 1)
    assert (anchored[3]["start_line"], anchored[3]["end_line"]) == (b_line, b_line + 1)
    assert splice(md, anchored, {}) == md, "配图块之后区间重叠了（splice 的无重叠不变量破了）"


def test_image_blocks_not_paired_when_counts_differ():
    """★ **计数不等就整趟不配** —— 配对是按顺序的，顺序一旦不一致就会**整批错标**，
    而错标比不标更坏（本仓既有口径）。"""
    md = "正文一。\n\n" + _IMG_A + "\n\n正文二。"
    bl = [_blk("正文一。", 0), _blk("", 1, label="image"),
          _blk("", 2, label="image"), _blk("正文二。", 3)]      # 2 个图块 vs 1 行 <img>
    anchored, st = anchor_blocks(md, bl)
    assert st["image_paired"] == 0, f"计数不等却配对了：{st}"
    assert all(b["start_line"] is None for b in anchored if b["label"] == "image")
    assert splice(md, anchored, {}) == md


def test_table_cell_images_are_not_paired_to_image_blocks():
    """★ **表格里的 `<img>` 归表块**，不能被拿去配图块（否则图块会整体错位一行）。

    判据是按 `table` 块的 span 排除 —— 不是按"这行长什么样"猜。
    """
    md = "正文一。\n\n" + _T_MD_1 + "\n\n" + _IMG_A + "\n\n正文二。"
    bl = [_blk("正文一。", 0), _blk(_T_BLK, 1, label="table"),
          _blk("", 2, label="image"), _blk("正文二。", 3)]
    anchored, st = anchor_blocks(md, bl)
    img = [b for b in anchored if b["label"] == "image"][0]
    lines = md.split("\n")
    assert st["image_paired"] == 1, f"配对数目不对：{st}"
    assert img["start_line"] == lines.index(_IMG_A), \
        "图块被配到了表格里那行 `<img>`（表格里的图应归表块）"


def test_reanchor_strips_stale_md_html():
    """★ **重锚已带 `md_html` 的块**时，未锚定分支不得把**旧值**原样带出（终审 finding #1）。

    `scripts/backfill_layout.py` 会对**已有** layout.json 的块重跑 `anchor_blocks`；
    而 `md_html` 只在**锚定成功**分支按区间重新切出来。若循环不做剥离，两条非成功分支
    （`idx not in anchorable` / `missed`）的 `{**blk, ...}` 会把上一轮的 `md_html` 带出 ⇒
    一个**未锚定**的表格块仍带着**过期的** md 原文（其区间已随重锚改变）⇒ 前端会拿它当编辑源。
    本用例即那条不变量的护栏：`md_html` 的**唯一来源**是成功分支。
    """
    base = "正文里没有表格。"
    stale = {"md_html": "<table><tr><td>过期的旧表</td></tr></table>"}
    bl = [{**_blk("正文里没有表格。", 0), **stale},
          {**_blk(_T_BLK, 1, label="table"), **stale}]          # 已播过 md_html 的两个块
    anchored, _ = anchor_blocks(base, bl)
    assert anchored[1]["start_line"] is None, "前置：表格块应锚不上（md 里没有它）"
    # ⚠️ **顺序是有意的**（终审修复的受限复审指出）：块 0 的内容与 md 相同 ⇒ 它走的是**成功**分支，
    #    所以「块 0 带出旧键」证的是**相邻的**另一处泄漏（成功分支对**非表格**块 `extra == {}`，
    #    `{**blk, **extra}` 同样会带出旧键）。**本条 finding 自己的分支是块 1 的 missed 分支**，
    #    故把块 1 的断言放在**前面**，让它成为变异时**首个**报错 ⇒ 该断言的红是**实测**到的，
    #    而不是靠推演说它「应该会红」。
    assert "md_html" not in anchored[1], (
        "未锚定的表格块把**上一轮的** md_html 原样带出（`{**blk, ...}` 未剥离）；"
        f"它的区间已随重锚改变，这段原文是**过期**的：{anchored[1].get('md_html')!r}")
    assert "md_html" not in anchored[0], "非表格块带出了旧的 md_html"
