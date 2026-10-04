"""layout 提取 / 脱敏 / 标签分类 / schema 校验 / 落盘读取（spec §5，计划 Task 2）。

夹具两套：
  ① tests/fixtures/ocr_layout_golden.jsonl —— **合成**黄金夹具，**入库**，CI 里真跑；
  ② data/_ocr_reference/raw.jsonl —— 真实夹具，**不入库**，缺则跳过
     （P0 验收时用 REQUIRE_LAYOUT_FIXTURE=1 让它**失败**而非跳过 —— 门禁不得 fail-open）。
"""
import json
import os
from pathlib import Path

import pytest

from app.ocr.layout import (LAYOUT_FILENAME, SIGNED_URL_KEYS, classify_labels,
                            extract_layout, load_layout, save_layout, sort_key_stats)

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "ocr_layout_golden.jsonl"
REF = REPO / "data" / "_ocr_reference" / "raw.jsonl"


def _recs(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").split("\n") if l.strip()]


def _require_ref() -> None:
    """要求真实夹具在场。P0 验收用 REQUIRE_LAYOUT_FIXTURE=1 跑；此时缺失是**失败**。"""
    if REF.is_file():
        return
    if os.environ.get("REQUIRE_LAYOUT_FIXTURE") == "1":
        pytest.fail("缺少真实夹具：P0 验收不得空过（见 scripts/capture_ocr_reference.py）")
    pytest.skip("缺少真实夹具 data/_ocr_reference/raw.jsonl")


# ── 提取 ──────────────────────────────────────────────

def test_extract_layout_from_golden_shape():
    lay = extract_layout(_recs(GOLDEN))
    assert lay["version"] == 1 and lay["pages"] and lay["blocks"]
    for p in lay["pages"]:
        assert p["index"] >= 1 and p["width"] and p["height"]
    for b in lay["blocks"]:
        assert b["label"] and isinstance(b["content"], str)
        assert b["page"] >= 1 and isinstance(b["bbox"], list) and len(b["bbox"]) == 4


def test_extract_layout_page_index_is_flat_across_records():
    """★ 页码 = 跨 record 的**扁平累计序号**，不是 record 下标。

    实测支撑：完整产物是 **19 条 record / 57 页** —— 一条 record 多页是**常态**，
    用 record 下标会让第 2..N 页撞号，左栏高亮定位到错页且完全静默。
    """
    def _page(tag, gid):
        return {"prunedResult": {"width": 10, "height": 20, "parsing_res_list": [
            {"block_label": "text", "block_content": tag, "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": gid}]}}

    rec = {"result": {"layoutParsingResults": [_page("p1", 0), _page("p2", 1)]}}
    lay = extract_layout([rec, rec])
    assert [p["index"] for p in lay["pages"]] == [1, 2, 3, 4], \
        "页码应为跨 record 的扁平累计序号；用 record 下标会让第 2..N 页撞号"
    assert [b["page"] for b in lay["blocks"]] == [1, 2, 3, 4]


def test_extract_layout_is_whitelist_so_signed_urls_cannot_survive():
    """★ 脱敏：输出是**白名单构造**的 ⇒ 签名 URL 不可能进来。

    用**含签名 URL 的合成输入**反证 —— 否则本用例是空转（两边都没有就什么也没证明）。
    """
    rec = {"result": {"layoutParsingResults": [{
        "prunedResult": {"width": 10, "height": 20, "parsing_res_list": [
            {"block_label": "text", "block_content": "x", "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": 0}]},
        "inputImage": "https://x/y?authorization=bce-auth-v1/SECRET",
        "preprocessedImages": ["https://x/y?authorization=bce-auth-v1/SECRET"],
        "outputImages": {"layout_det_res": "https://x/y?authorization=bce-auth-v1/SECRET"}}]}}
    dumped = json.dumps(extract_layout([rec]), ensure_ascii=False)
    assert "authorization=" not in dumped and "SECRET" not in dumped
    for k in SIGNED_URL_KEYS:
        assert k not in dumped


def test_extract_layout_skips_malformed_block_entries():
    """畸形块（非 dict）不得让整条链中断，也不得抛出"""
    rec = {"result": {"layoutParsingResults": [{"prunedResult": {
        "width": 1, "height": 1, "parsing_res_list": [
            "not-a-dict", None,
            {"block_label": "text", "block_content": "ok", "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": 0}]}}]}}
    blocks = extract_layout([rec])["blocks"]
    assert len(blocks) == 1 and blocks[0]["content"] == "ok"


# ── 排序键 ─────────────────────────────────────────────

def test_sort_key_stats_reports_monotonic_and_missing():
    ok = [{"global_block_id": 0}, {"global_block_id": 1}, {"global_block_id": 2}]
    assert sort_key_stats(ok) == {"all_have_gid": True, "monotonic": True}

    missing = [{"global_block_id": 0}, {"global_block_id": None}]
    assert sort_key_stats(missing)["all_have_gid"] is False

    out_of_order = [{"global_block_id": 2}, {"global_block_id": 0}]
    assert sort_key_stats(out_of_order)["monotonic"] is False


# ── 标签分类 ───────────────────────────────────────────

def test_classify_labels_exempts_not_in_markdown_and_empty_content():
    """★ 门禁**不能一刀切** `missed == 0`。

    实测：41 个未命中里有 30 个是 `formula_number`(21) + `image`(9) —— 它们
    **本就不在 `markdown.text` 里**，未命中是**正确行为**，不是缺陷。
    故必须分类，且豁免**理由可追溯**。
    """
    blocks = [{"label": "text", "content": "正文", "page": 1, "bbox": [0, 0, 1, 1],
               "order": 1, "global_block_id": 0},
              {"label": "text", "content": "   ", "page": 1, "bbox": [0, 0, 1, 1],
               "order": 2, "global_block_id": 1},
              {"label": "number", "content": "第1页", "page": 1, "bbox": [0, 0, 1, 1],
               "order": 3, "global_block_id": 2}]
    cls = classify_labels(blocks)
    assert cls["anchorable"] == [0]
    assert sorted(cls["exempt"]) == [1, 2]
    # 理由必须**区分**：标签类 vs 空内容类（不能笼统一句"豁免"）
    assert "内容为空" in cls["exempt_reasons"][1]
    assert "number" in cls["exempt_reasons"][2]


def test_classify_labels_prefers_label_reason_over_empty_content():
    """`image` 同时命中两个豁免条件 —— 应报**更具体的标签理由**（便于追溯成因）"""
    blocks = [{"label": "image", "content": "", "page": 1, "bbox": [0, 0, 1, 1],
               "order": 1, "global_block_id": 0}]
    reasons = classify_labels(blocks)["exempt_reasons"]
    assert "标签" in reasons[0] and "image" in reasons[0]


def test_classify_labels_honours_effective_ignore_labels():
    """生效参数（`markdownIgnoreLabels`）会**扩展**豁免名单 —— 不得只认硬编码"""
    blocks = [{"label": "custom_noise", "content": "噪声", "page": 1,
               "bbox": [0, 0, 1, 1], "order": 1, "global_block_id": 0}]
    assert classify_labels(blocks)["anchorable"] == [0]
    assert classify_labels(blocks, ["custom_noise"])["exempt"] == [0]


# ── 落盘 / 读取 ────────────────────────────────────────

def test_save_then_load_roundtrip(tmp_path):
    lay = {"version": 1, "pages": [{"index": 1, "width": 10, "height": 20}],
           "blocks": [{"label": "text", "content": "x", "page": 1, "bbox": [0, 0, 1, 1],
                       "order": 1, "global_block_id": 0,
                       "start_line": 0, "end_line": 1, "prefix": ""}]}
    path = save_layout(lay, tmp_path)
    assert Path(path).name == LAYOUT_FILENAME
    assert load_layout(tmp_path) == lay


def test_load_layout_missing_returns_none(tmp_path):
    assert load_layout(tmp_path) is None


def test_load_layout_corrupt_returns_none(tmp_path):
    (tmp_path / LAYOUT_FILENAME).write_text("{ not json", encoding="utf-8")
    assert load_layout(tmp_path) is None


def test_load_layout_non_dict_returns_none(tmp_path):
    for bad in ("[1, 2]", '"str"', "null"):
        (tmp_path / LAYOUT_FILENAME).write_text(bad, encoding="utf-8")
        assert load_layout(tmp_path) is None, f"{bad} 应返回 None"


def test_load_layout_bad_schema_returns_none(tmp_path):
    """★ 严格 schema：合法 JSON、是 dict，但版本/类型/区间不符 ⇒ 仍返回 None。

    只查「version 是 int、pages/blocks 是 list」太松：`{"version":99,...}`、
    `blocks=["x"]`、越界/倒置 span 都能过，而 P1 会据此渲染炸掉。
    """
    bad_payloads = [
        '{"foo": 1}',
        '{"version": 99, "pages": [{"index": 1}], "blocks": []}',
        '{"version": 1, "pages": [], "blocks": []}',
        '{"version": 1, "pages": "x", "blocks": []}',
        '{"version": 1, "pages": [{"index": 1}], "blocks": ["x"]}',
        '{"version": 1, "pages": [{"index": 1}], "blocks": [{"label": "a", "content": "b", "start_line": 5, "end_line": 2}]}',
        '{"version": 1, "pages": [{"index": 1}], "blocks": [{"label": "a", "content": "b", "start_line": -1, "end_line": 3}]}',
    ]
    for bad in bad_payloads:
        (tmp_path / LAYOUT_FILENAME).write_text(bad, encoding="utf-8")
        assert load_layout(tmp_path) is None, f"{bad} 应判为 schema 不符"


def test_load_layout_bad_utf8_returns_none(tmp_path):
    (tmp_path / LAYOUT_FILENAME).write_bytes(b"\xff\xfe\x00not utf8")
    assert load_layout(tmp_path) is None


def test_save_layout_leaves_no_tmp_file(tmp_path):
    """原子写：成功后不留 .tmp 残留"""
    save_layout({"version": 1, "pages": [{"index": 1, "width": 1, "height": 1}],
                 "blocks": []}, tmp_path)
    assert not list(tmp_path.glob("*.tmp"))


# ── 真实夹具（缺则跳过；P0 验收时必须真跑）────────────────

def test_real_fixture_extracts_and_is_scrubbed():
    _require_ref()
    lay = extract_layout(_recs(REF))
    assert len(lay["pages"]) > 19, "页数应 > record 数（证明一条 record 可含多页）"
    assert len(lay["blocks"]) > 0
    dumped = json.dumps(lay, ensure_ascii=False)
    for k in SIGNED_URL_KEYS:
        assert k not in dumped
    stats = sort_key_stats(lay["blocks"])
    assert stats["all_have_gid"] is True, "真实夹具的块应全部带 global_block_id"
    print(f"\n真实夹具：页 {len(lay['pages'])} / 块 {len(lay['blocks'])} / {stats}")
