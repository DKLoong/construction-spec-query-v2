"""块 ↔ 条文 对齐（spec §11，P1 计划 Task 1）。

**不自己扫编号**：用 `parse_markdown(..., _decisions=)` 的逐行判定 —— 那是 parser
**自己的**尺子。自己再实现一遍编号匹配会变成「第二把尺子」，判据一旦与 parser
漂移就**静默错标**（本仓 memory 明确警告过这一类）。

关键：`_Decision.line` 与 P0 锚定的 `start_line` **同一坐标系**（都是
`md_text.split("\\n")` 的下标）⇒ 对齐退化为一次二分查找。
"""
import json
import os
from pathlib import Path

import pytest

from app.ocr.layout import align_clauses, anchor_blocks, extract_layout

REPO = Path(__file__).resolve().parent.parent
REF_DIR = REPO / "data" / "_ocr_reference"


def _require_ref() -> None:
    if (REF_DIR / "raw.jsonl").is_file() and (REF_DIR / "reference.md").is_file():
        return
    if os.environ.get("REQUIRE_LAYOUT_FIXTURE") == "1":
        pytest.fail("缺少真实夹具：P1 验收不得空过")
    pytest.skip("缺少真实夹具 data/_ocr_reference/{raw.jsonl,reference.md}")


def _blk(content, start_line, gid):
    return {"label": "text", "content": content, "page": 1, "bbox": [0, 0, 1, 1],
            "order": gid, "global_block_id": gid,
            "start_line": start_line, "end_line": None if start_line is None else start_line + 1,
            "prefix": "", "suffix": ""}


MD = "3.0.1 第一条。\n\n3.0.2 第二条。\n\n3.0.3 第三条。"


def test_block_gets_the_clause_that_starts_at_or_before_it():
    """块所属条文 = **起始行 ≤ 块起始行的最后一个候选行**"""
    blocks = [_blk("第一条。", 0, 0), _blk("第二条。", 2, 1), _blk("第三条。", 4, 2)]
    out, stats = align_clauses(MD, blocks)
    assert [b["clause_no"] for b in out] == ["3.0.1", "3.0.2", "3.0.3"]
    assert stats["labeled"] == 3 and stats["unlabeled"] == 0
    assert stats["distinct_clauses"] == 3


def test_leading_block_before_first_clause_is_unlabeled():
    """**前导块**（第一个候选行之前）标 `None` —— spec §11.2：无归属就不标，不猜"""
    md = "封面文字\n\n3.0.1 第一条。"
    blocks = [_blk("封面文字", 0, 0), _blk("第一条。", 2, 1)]
    out, stats = align_clauses(md, blocks)
    assert out[0]["clause_no"] is None
    assert out[1]["clause_no"] == "3.0.1"
    assert stats["labeled"] == 1 and stats["unlabeled"] == 1


def test_unanchored_block_is_unlabeled_not_guessed():
    """未锚定的块（`start_line is None`）同样 `None` —— 绝不用邻居的条文号兜底"""
    blocks = [_blk("第一条。", 0, 0),
              {**_blk("", None, 1), "label": "table"}]
    out, stats = align_clauses(MD, blocks)
    assert out[0]["clause_no"] == "3.0.1"
    assert out[1]["clause_no"] is None
    assert stats["unlabeled"] == 1


def test_multi_line_block_spanning_into_next_clause_keeps_its_start_clause():
    """跨到下一条文里的块按**起始行**归属，不按结束行"""
    blocks = [{**_blk("第一条。\n\n3.0.2", 0, 0), "end_line": 4}]
    out, _ = align_clauses(MD, blocks)
    assert out[0]["clause_no"] == "3.0.1"


def test_empty_md_yields_all_unlabeled():
    """没有候选行的文档（纯文本）⇒ 全部 None，且不抛"""
    out, stats = align_clauses("没有任何编号的内容", [_blk("内容", 0, 0)])
    assert out[0]["clause_no"] is None
    assert stats["distinct_clauses"] == 0


def test_original_order_is_preserved():
    """输入按任意顺序传入，输出**保持同一顺序**（调用方按下标索引，乱序即错位）"""
    blocks = [_blk("第三条。", 4, 2), _blk("第一条。", 0, 0), _blk("第二条。", 2, 1)]
    out, _ = align_clauses(MD, blocks)
    assert [b["global_block_id"] for b in out] == [2, 0, 1]
    assert [b["clause_no"] for b in out] == ["3.0.3", "3.0.1", "3.0.2"]


def test_input_blocks_are_not_mutated():
    """纯函数：不得就地改传入的块（调用方可能还持有原列表）"""
    blocks = [_blk("第一条。", 0, 0)]
    align_clauses(MD, blocks)
    assert "clause_no" not in blocks[0]


def test_real_fixture_aligns_most_blocks():
    """★ 真实夹具实测：已锚定的块里应当**绝大多数**拿到条文号。

    未锚定的 2 个（table/chart）必然无条文号。**未标注率超 5% 就说明对齐有问题**，
    不是"数据如此"。
    """
    _require_ref()
    md = (REF_DIR / "reference.md").read_text(encoding="utf-8")
    blocks = extract_layout([json.loads(l) for l in
                             (REF_DIR / "raw.jsonl").read_text(encoding="utf-8").split("\n")
                             if l.strip()])["blocks"]
    anchored, _ = anchor_blocks(md, blocks)

    out, stats = align_clauses(md, anchored)
    anchored_count = sum(1 for b in anchored if b["start_line"] is not None)
    assert stats["blocks"] == len(anchored)
    assert stats["distinct_clauses"] > 0, "全文竟无一条条文号？夹具或算法有误"

    unlabeled_rate = (anchored_count - stats["labeled"]) / anchored_count
    assert unlabeled_rate < 0.05, (
        f"已锚定块却有 {unlabeled_rate:.1%} 拿不到条文号 ⇒ 对齐有问题")
    print(f"\n对齐：块 {stats['blocks']} 已标注 {stats['labeled']} "
          f"未标注 {stats['unlabeled']} 覆盖条文 {stats['distinct_clauses']} 条")
