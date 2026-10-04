"""OCR 版面块（layout）的提取、脱敏、标签分类、锚定、拼接、落盘与读取。

设计依据：spec `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`
§3.2 / §3.3 / §5 / §10，以及实施计划 `docs/superpowers/plans/2026-10-04-ocr-review-p0-layout-foundation.md`
**§0 的真机实测事实**。

**核心设计（span 方案）**：真源是 OCR 自己产出的 `markdown.text`（**未清洗**），
**不重建**。每个块锚定到基准文本的**行区间**（含被剥掉的 `#` 前缀）；拼接时
**未编辑的区间原样取回原行** ⇒ 「不编辑即逐字节相等」是**结构上恒真**的。

本模块**纯函数 + 文件 IO**：不依赖 OCR 客户端、不碰数据库，故可单测，
将来也能让前端通过一个轻端点复用同一份锚定/拼接规则（spec §10.1「规则只写一份」）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
LAYOUT_FILENAME = "layout.json"
LAYOUT_RAW_FILENAME = "layout.raw.json"

# 官网响应里这些字段是**带凭据的签名 URL**；本模块白名单式提取，天然不会带上它们。
# 该元组仅供测试断言「确实没出现」。
SIGNED_URL_KEYS = ("inputImage", "preprocessedImages", "outputImages")

# ⚠️ **实测**（2026-10-04，完整 57 页）**不进 `markdown.text`** 的标签：
#   - 与请求参数 `markdownIgnoreLabels` 对齐的：header/footer/number/footnote/aside_text…
#   - **实测额外发现**：`formula_number`(21) 与 `image`(9) 的 `block_content`
#     **也不出现在 `markdown.text` 里**（grep 无命中），而它们**不在**
#     `markdownIgnoreLabels` 里。
# ⇒ 本名单**不是**从参数抄的，而是「参数 ∪ 实测」。刷新夹具时须用计划 Task 1 Step 4 复核。
DEFAULT_NOT_IN_MARKDOWN = frozenset({
    "header", "header_image", "footer", "footer_image",
    "number", "footnote", "aside_text",
    "formula_number", "image",
})


def sort_key_stats(blocks: list[dict]) -> dict:
    """报告排序键的可用性（供计划 Task 1 Step 4 的实测复核用）。

    `global_block_id` 实测**全序单调**（文档级全局值），是可靠主序；
    但必须能把「有没有缺 gid」「是否单调」报出来，故抽成函数。
    """
    gids = [b.get("global_block_id") for b in blocks]
    if not all(g is not None for g in gids):
        return {"all_have_gid": False, "monotonic": False}
    # 显式收敛类型：`all(...)` 的布尔结果**不能**帮类型检查器窄化列表元素
    ints = [int(g) for g in gids if g is not None]
    return {
        "all_have_gid": True,
        "monotonic": all(ints[i] <= ints[i + 1] for i in range(len(ints) - 1)),
    }


def extract_layout(records: list[dict]) -> dict:
    """OCR JSONL 记录 → 精简 layout 结构（**已脱敏**、**尚未锚定**）。

    只读 `prunedResult.parsing_res_list` 与 `prunedResult.width|height`。
    `markdown`（只有 text/images）不参与 —— 它是**基准文本**，由调用方另行提供。

    **白名单式**：输出字段全是显式构造的，响应中的签名 URL 不可能被带进来。

    ⚠️ 页码是**跨 record 的扁平累计序号**。实测：完整产物是 19 条 record / 57 页
    —— 一条 record 多页是常态，用 record 下标会让第 2..N 页**撞号**（静默错页）。
    """
    pages: list[dict] = []
    blocks: list[dict] = []

    page_no = 0
    for rec in records:
        for lp in (rec.get("result") or {}).get("layoutParsingResults") or []:
            page_no += 1
            pr = lp.get("prunedResult") or {}
            pages.append({
                "index": page_no,
                "width": pr.get("width"),
                "height": pr.get("height"),
            })
            for b in pr.get("parsing_res_list") or []:
                if not isinstance(b, dict):        # 畸形项不得中断整条链
                    continue
                blocks.append({
                    "label": b.get("block_label") or "",
                    "content": b.get("block_content") or "",
                    "page": page_no,
                    "bbox": b.get("block_bbox"),
                    "order": b.get("block_order"),
                    "global_block_id": b.get("global_block_id"),
                })

    return {"version": LAYOUT_VERSION, "pages": pages, "blocks": blocks}


def classify_labels(blocks: list[dict], ignore_labels=None) -> dict:
    """把块分成「可锚定」与「豁免」，并给出**逐条豁免理由**。

    ⚠️ 门禁**不能一刀切** `missed == 0`：实测 41 个未命中里有 30 个是
    `formula_number`(21) + `image`(9) —— 它们**本就不在 `markdown.text` 里**，
    未命中是**正确行为**，不是缺陷。所以必须分类，且理由必须可追溯。

    `ignore_labels` 传**本次生效**的 `markdownIgnoreLabels`（来自 OCR 参数，
    会记进 `layout.json`）；未传则只用 `DEFAULT_NOT_IN_MARKDOWN`。
    """
    not_in_md = set(DEFAULT_NOT_IN_MARKDOWN) | set(ignore_labels or [])
    anchorable: list[int] = []
    exempt: list[int] = []
    reasons: dict[int, str] = {}
    for i, b in enumerate(blocks):
        label = b.get("label") or ""
        if label in not_in_md:
            exempt.append(i)
            reasons[i] = f"标签 {label!r} 不进 markdown.text"
        elif not (b.get("content") or "").strip():
            exempt.append(i)
            reasons[i] = "内容为空"
        else:
            anchorable.append(i)
    return {"anchorable": anchorable, "exempt": exempt, "exempt_reasons": reasons,
            "not_in_markdown": sorted(not_in_md)}


def base_sha256(text: str) -> str:
    """基准文本指纹 —— 加载时用来确认 layout 与文本仍是同一份（防「旧 span + 新文本」错配）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_layout(layout: dict, output_dir: str | Path) -> str:
    """落盘（原子写：先写 .tmp 再 replace），返回文件路径。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / LAYOUT_FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def _is_valid_layout(data: object) -> bool:
    """**严格** schema 校验。

    只查「version 是 int、pages/blocks 是 list」太松：
    `{"version":99,...}`、`blocks=["x"]`、越界/倒置 span 都能过，
    而消费方会据此渲染炸掉。这里逐项查版本、类型与区间。
    """
    if not isinstance(data, dict):
        return False
    if data.get("version") != LAYOUT_VERSION:
        return False
    pages, blocks = data.get("pages"), data.get("blocks")
    if not isinstance(pages, list) or not isinstance(blocks, list):
        return False
    if not pages:                                  # 空结构视为不可用
        return False
    for p in pages:
        if not isinstance(p, dict) or not isinstance(p.get("index"), int):
            return False
    for b in blocks:
        if not isinstance(b, dict) or not isinstance(b.get("label"), str):
            return False
        if not isinstance(b.get("content"), str):
            return False
        s, e = b.get("start_line"), b.get("end_line")
        if s is None and e is None:
            continue                               # 未锚定：合法
        if not (isinstance(s, int) and isinstance(e, int)) or s < 0 or e <= s:
            return False
    return True


def load_layout(output_dir: str | Path) -> dict | None:
    """读 `layout.json`；**缺失 / 损坏 / schema 不符一律 None**（调用方据此降级，spec §13）。

    刻意不抛：审查页没有可用 layout 时应退化为纯预览，而不是 500。
    """
    path = Path(output_dir) / LAYOUT_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("layout.json 读取失败（按缺失处理）: %s", path)
        return None
    if not _is_valid_layout(data):
        logger.warning("layout.json schema 不符（按缺失处理）: %s", path)
        return None
    return data
