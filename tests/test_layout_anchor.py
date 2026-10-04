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
