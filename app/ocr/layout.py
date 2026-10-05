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

import bisect
import hashlib
import json
import logging
import re
from pathlib import Path

from app.parser.md_parser import _Decision, parse_markdown

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

# 审查页 P2 可**行级编辑**的 label —— **单一来源**（前端从 /layout 拿，不在 JS 里另写一份）。
# 依据：2026-10-05 实测夹具 label 分布（text 477 / paragraph_title 47 / figure_title 9 /
# content 4 / vision_footnote 3，全部有 span），其余 label 各归其期：
# display_formula → P4；table / chart → P3；image / number / formula_number → 不进 markdown（豁免）。
# 注意 `chart` 虽是纯文本 5 行，但**未锚定**（夹具 idx 522）⇒ 会被 canEdit 的 span 判据挡下。
EDITABLE_TEXT_LABELS = frozenset({
    "doc_title", "paragraph_title", "content", "text", "figure_title", "vision_footnote",
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


# ═══════════════════════════════════════════════════════════
# 锚定与拼接（计划 Task 3）
# ═══════════════════════════════════════════════════════════

_LEADING_HASH = re.compile(r"^(#{1,6}\s+)")
_WRAPPING = re.compile(r"^(<\w+[^>]*>)(.*)(</\w+>)$")


def _split_affixes(line: str) -> tuple[str, str, str]:
    """把一行的「内容」与「前后缀」拆开：返回 `(内容, 前缀, 后缀)`。

    两条**实测**来源（见计划 §0 与 Task 3 Step 5 的诊断）：
    - `## 目次` → `("目次", "## ", "")` —— 标题型块
    - `<div style="text-align: center;">图 8.0.2 (mm)</div>`
      → `("图 8.0.2 (mm)", "<div …>", "</div>")` —— markdown 给**居中图题**包的壳

    前后缀会**记进 span**，供 `splice` 原样保留。少了它：
    - 编辑标题会丢 `## ` ⇒ 改变 `parse_markdown` 的**标题型判定**
    - 编辑图题会丢居中 div ⇒ 图注**变左对齐**（正是审查页要修的那类问题）
    """
    s = line.strip()
    m = _WRAPPING.match(s)
    if m:
        return m.group(2).strip(), m.group(1), m.group(3)
    m = _LEADING_HASH.match(s)
    if m:
        return s[m.end():].strip(), m.group(1), ""
    return s.rstrip(), "", ""


def _norm(line: str) -> str:
    """归一化之一：**去行尾空白**。

    实测差异：`block_content` 是 `.....4␣␣`，`markdown.text` 是 `.....4␣␣␣␣`。
    """
    return line.rstrip()


def _find_candidates(lines: list[str], content: str, start: int,
                     max_gap: int = 2, limit: int = 2) -> list[tuple[int, int, str, str]]:
    """在 `lines[start:]` 里找 `content` 的候选区间，最多收 `limit` 个。

    **四条归一化**（每条都由真机实测逼出，见计划 §0 与 Task 3 Step 5 的诊断）：
    1. **去行尾空白**（`_norm`）
    2. **首行允许原文多一个 `#` 前缀**（记进 prefix，供 splice 保留）
    3. **块内行之间容忍 ≤`max_gap` 个空行** —— 实测：`markdown.text` 把块内每行
       渲染成**独立段落**；只容忍连续行的旧规则在完整文档上漏掉 **52** 个块
    4. **首行允许被 HTML 标签整行包裹**（记进 prefix/suffix）—— 实测：`markdown.text`
       把居中图题写成 `<div style="text-align: center;">图 8.0.2 (mm)</div>`，
       不剥则 **9 个 `figure_title` 全部锚不上**，图题在审查页将**不可编辑**

    返回 `[(start_line, end_line, prefix, suffix)]`（半开区间）。收 2 个是为了让
    调用方能报出「重复候选」这个诊断指标，但**不据此拒绝锚定**（顺序本身消解重复）。
    """
    want = [_norm(x) for x in content.split("\n")]
    while want and not want[-1]:
        want.pop()
    if not want:
        return []

    out: list[tuple[int, int, str, str]] = []
    n_lines = len(lines)
    for i in range(start, n_lines):
        inner, prefix, suffix = _split_affixes(lines[i])
        if inner != want[0] and _norm(lines[i]) != want[0]:
            continue

        if len(want) == 1:
            out.append((i, i + 1, prefix, suffix))
        else:
            j, ok = i + 1, True
            for k in range(1, len(want)):
                skipped = 0
                while j < n_lines and not _norm(lines[j]):
                    skipped += 1
                    j += 1
                    if skipped > max_gap:
                        break
                if skipped > max_gap or j >= n_lines or _norm(lines[j]) != want[k]:
                    ok = False
                    break
                if k == len(want) - 1:
                    out.append((i, j + 1, prefix, suffix))
                j += 1
            if not ok:
                continue

        if len(out) >= limit:
            return out
    return out


def _sorted_blocks_with_index(blocks: list[dict]) -> list[dict]:
    """按阅读顺序排序，并把**原下标**带进结果（`edits` 用的就是它）。

    实测：被忽略的 `number` 块的 `block_order` 为 **null**，不能单拿 order 排序；
    `global_block_id` 实测**全序单调**（文档级全局值），是可靠主序。
    ⚠️ **键必须落在同一空间**：不能"有 gid 用 gid、没有用 page*100000+order" ——
    混用会把缺 gid 的块推到极端。故**只要有一个块缺 gid，整体退化**为 (page, order)。
    """
    tagged = [{"idx": i, **b} for i, b in enumerate(blocks)]
    if all(t.get("global_block_id") is not None for t in tagged):
        tagged.sort(key=lambda t: int(t["global_block_id"]))
    else:
        logger.warning("部分块缺 global_block_id，整体退化为 (page, order) 排序")
        tagged.sort(key=lambda t: (t.get("page") or 0, t.get("order") or 0))
    return tagged


def anchor_blocks(base_text: str, blocks: list[dict],
                  ignore_labels=None) -> tuple[list[dict], dict]:
    """把块锚定到**基准文本的行区间**；返回 `(带 span 的 blocks, 统计)`。

    - 区间以**行号**表达（半开 `[start_line, end_line)`），并记录被剥掉的 `prefix`
    - **贪心 + 游标**：按阅读顺序取**首个**候选 —— 顺序本身消解重复，故「内容出现
      两次」**不是**歧义（这是对计划原稿的设计纠正，见 `tests/test_layout_anchor.py` 顶部）
    - **游标可信度**：某个可锚定块漏锚 ⇒ 游标不动、标记 `stale`；此后**第一个**成功
      锚定记入 `stale_anchors` 并清除 `stale`。这才是错位风险的真实指标 ——
      比"多候选即拒绝"更准：只惩罚真实风险，不惩罚合法重复
    - **豁免类**（`classify_labels` 判定的 `not_in_markdown` 或空内容）**不计入 missed**
    - **未锚定不抛**，全部记进 `stats`，由调用方判阈值
    """
    cls = classify_labels(blocks, ignore_labels)
    anchorable = set(cls["anchorable"])
    lines = base_text.split("\n")

    cursor = 0
    anchored = missed = repeat_candidates = stale_anchors = 0
    stale = False
    residual: list[tuple[int, str, str]] = []
    out: list[dict] = []

    for entry in _sorted_blocks_with_index(blocks):
        idx = entry["idx"]
        blk = {k: v for k, v in entry.items() if k != "idx"}

        if idx not in anchorable:
            out.append({**blk, "start_line": None, "end_line": None,
                        "prefix": "", "suffix": ""})
            continue

        cands = _find_candidates(lines, blk.get("content") or "", cursor)
        if not cands:
            missed += 1
            stale = True                     # 游标停在原地；其后锚定不可信
            residual.append((idx, blk.get("label") or "",
                             (blk.get("content") or "").split("\n")[0][:48]))
            out.append({**blk, "start_line": None, "end_line": None,
                        "prefix": "", "suffix": ""})
            continue

        if len(cands) > 1:
            repeat_candidates += 1           # 诊断指标：不据此拒绝
        if stale:
            stale_anchors += 1
            stale = False                    # 第一个成功锚定重新确立位置

        s, e, prefix, suffix = cands[0]
        cursor = e
        anchored += 1
        out.append({**blk, "start_line": s, "end_line": e,
                    "prefix": prefix, "suffix": suffix})

    return out, {
        "total": len(blocks),
        "exempt": len(cls["exempt"]),
        "anchorable": len(anchorable),
        "anchored": anchored,
        "missed": missed,
        "stale_anchors": stale_anchors,
        "repeat_candidates": repeat_candidates,
        "residual_miss": residual,
        "not_in_markdown": cls["not_in_markdown"],
    }


def splice(base_text: str, blocks: list[dict], edits: dict[int, str]) -> str:
    """按区间拼回文本：**只替换 `edits` 里的块**，其余区间原样取回原行。

    - `edits` 的键是 `blocks` 的**下标**（与 `anchor_blocks` 返回的列表同序）
    - 编辑块时**保留其 `prefix` / `suffix`**：否则改标题会丢 `## `（改变
      `parse_markdown` 的标题型判定），改图题会丢 `<div style="text-align:center">`
      （图注变左对齐）
    - **区间重叠 ⇒ 抛 `ValueError`**：静默跳过 = 用户那次编辑被悄悄丢掉，比报错坏得多

    ⚠️ 这是本方案的地基：`edits` 为空时等价于 `base_text` 本身（**逐字节**），
    因为未编辑区间是**原样取回原行**而不是"重新生成" —— 故"往返保真"无需证明。
    """
    lines = base_text.split("\n")
    ops = sorted(
        ((b["start_line"], b["end_line"], b.get("prefix") or "", b.get("suffix") or "",
          edits.get(i))
         for i, b in enumerate(blocks) if b.get("start_line") is not None),
        key=lambda t: t[0],
    )

    out: list[str] = []
    cursor = 0
    for s, e, prefix, suffix, new in ops:
        if s < cursor:
            # 区间重叠是编程错误（anchor_blocks 的游标保证单调，不该出现）。
            # **必须显式失败**：静默跳过 = 用户那次编辑被悄悄丢掉，比报错坏得多。
            raise ValueError(f"layout 区间重叠：[{s}, {e}) 与游标 {cursor}")
        out.extend(lines[cursor:s])
        if new is None:
            out.extend(lines[s:e])           # 未编辑 ⇒ 原行原样
        else:
            # 保留前后缀；用户文本**原样写出**（不 rstrip，免得吃掉用户有意输入的
            # markdown 硬换行尾空格）
            new_lines = new.split("\n")
            first = prefix + (new_lines[0] if new_lines else "") + suffix
            out.extend([first, *new_lines[1:]])
        cursor = e
    out.extend(lines[cursor:])
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════
# 两阶段落盘（计划 Task 4）
# ═══════════════════════════════════════════════════════════

def _write_json_atomic(payload: dict, path: Path) -> None:
    """原子写：先写 `.tmp` 再 `replace`。抽出来便于单测注入失败。"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def save_raw_layout_safely(records: list[dict], output_dir: str | None,
                           params: dict | None = None) -> None:
    """**第一阶段**：把**未锚定**的块落 `layout.raw.json`；失败只记 WARN、**绝不抛出**。

    为什么分两阶段：锚定必须以**审查页实际使用的那份文本**为基准，而那是
    `_process_import` 处理后的产物，OCR 客户端看不到。

    ⚠️ **blocks 为空时不落盘**（否则审查页渲染空白页，绕过 spec §13 的降级契约）。
    ⚠️ 异常**必须收窄**（全局规则 §1.2）。`params` 一并存下，供锚定阶段用**同一份**
    `markdownIgnoreLabels` —— 不得把动态参数当固定事实。
    """
    if not output_dir:
        return
    try:
        layout = extract_layout(records)
        if not layout["blocks"]:
            logger.warning("layout 为空（响应可能不含 parsing_res_list），不落盘: %s", output_dir)
            return
        layout["ocr_params"] = dict(params or {})
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(layout, out / LAYOUT_RAW_FILENAME)
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as e:
        logger.warning("layout.raw.json 落盘失败（不影响导入）: %s", e)


def finalize_layout(output_dir: str | Path, base_text: str,
                    ignore_labels=None) -> dict | None:
    """**第二阶段**：消费 `layout.raw.json` → 锚定到 `base_text` → 落 `layout.json`。

    写下的 `layout.json` 含：`blocks`（带 span + prefix/suffix）、`base_sha256`（指纹）、
    `ocr_params`（生效参数）、`stats`。指纹用于防止「**旧 span + 新文本**」错配 ——
    故重跑时会**先删旧 `layout.json`**。

    **失败只记 WARN、绝不抛出**，且**失败时保留 raw** 便于排查/重试。
    返回锚定统计；无 raw 时返回 `None`（老任务 / 其他后端路径）。
    """
    out = Path(output_dir)
    raw_path = out / LAYOUT_RAW_FILENAME
    if not raw_path.is_file():
        return None
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        params = raw.get("ocr_params") or {}
        ignore = ignore_labels if ignore_labels is not None else params.get("markdownIgnoreLabels")
        anchored, stats = anchor_blocks(base_text, raw.get("blocks") or [], ignore)
        # spec §11.2：块 → 条文号。**必须在这里落库**——审查页只读 layout.json，
        # 不在请求期重算（那会让 /layout 端点每次请求都跑一遍 parse_markdown）。
        blocks, align_stats = _align_or_degrade(base_text, anchored)
        layout = {
            "version": LAYOUT_VERSION,
            "pages": raw.get("pages") or [],
            "blocks": blocks,
            "base_sha256": base_sha256(base_text),
            "ocr_params": params,
            "stats": {**{k: v for k, v in stats.items() if k != "residual_miss"},
                      **align_stats},
        }
        # ⚠️ 先清旧 layout.json，避免"旧 span + 新文本"错配
        (out / LAYOUT_FILENAME).unlink(missing_ok=True)
        save_layout(layout, out)
        if stats["missed"]:
            logger.warning("layout 锚定：未命中 %d / 可锚定 %d（详见 layout.json.stats）: %s",
                           stats["missed"], stats["anchorable"], out)
        # ✅ 只有**成功**才删 raw；失败保留以便排查/重试
        raw_path.unlink(missing_ok=True)
        # 返回值在原锚定 stats 之上并上条号统计（加 `clause_` 前缀，键不冲突），
        # 使调用方拿到的 stats 与落盘的 layout.json.stats 口径一致。
        return {**stats, **align_stats}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError,
            TypeError, ValueError, AttributeError) as e:
        logger.warning("layout 锚定失败（不影响导入；**保留 %s 便于排查**）: %s",
                       LAYOUT_RAW_FILENAME, e)
        return None


# ═══════════════════════════════════════════════════════════
# 块 → 条文号（spec §11）
# ═══════════════════════════════════════════════════════════

def align_clauses(md_text: str, blocks: list[dict]) -> tuple[list[dict], dict]:
    """给每个块标出它所属的**条文号**（spec §11）；返回 `(blocks, stats)`。

    **为什么用 `parse_markdown` 的诊断钩子而不是自己扫编号**：
    自己实现一遍编号匹配 = 本仓反复警告的「**第二把尺子**」。parser 的判据
    （R14 兄弟表决、内节点判据、同号重复拒绝、目次过滤…）复杂且会演进；
    私有的第二套判据一旦漂移就**静默错标**，而错标比不标更坏。

    `_decisions` 是 parser **自己**逐行输出的判定，且其 `line` 与锚定的
    `start_line` **同一坐标系**（都是 `md_text.split("\\n")` 的下标）——
    故对齐退化为一次二分查找。

    - 规则：块所属条文 = **起始行 ≤ 块起始行** 的**最后一个** `kind == "cand"` 行
    - 前导块（第一个候选行之前）与未锚定块 ⇒ `None`（spec §11.2：**不猜**）
    - **纯函数**：不改传入的块（返回新 dict）
    - 只读 `_decisions`，**不碰 parser 的判定逻辑**
    """
    decisions: list[_Decision] = []
    parse_markdown(md_text, _decisions=decisions)

    cand_at: dict[int, str] = {
        d.line: d.clause_no for d in decisions
        if d.kind == "cand" and d.clause_no
    }
    starts = sorted(cand_at)

    out: list[dict] = []
    labeled = unlabeled = 0
    seen: set[str] = set()
    for b in blocks:
        s = b.get("start_line")
        clause: str | None = None
        if s is not None and starts:
            i = bisect.bisect_right(starts, s) - 1
            if i >= 0:
                clause = cand_at[starts[i]]
        if clause is None:
            unlabeled += 1
        else:
            labeled += 1
            seen.add(clause)
        out.append({**b, "clause_no": clause})

    return out, {
        "blocks": len(blocks),
        "labeled": labeled,
        "unlabeled": unlabeled,
        "distinct_clauses": len(seen),
    }


def _align_or_degrade(md_text: str, blocks: list[dict]) -> tuple[list[dict], dict]:
    """条号对齐的**降级包装**：任何异常都退回「全部无条号」，绝不向上抛。

    与 `finalize_layout` 里锚定失败的处理口径一致：**保住 layout 主体**，
    只损失增强字段。返回的 stats 键统一加 `clause_` 前缀，使成功/降级
    两分支产出**同构**的四个键，也不与锚定 stats 的原生键名混淆。
    """
    try:
        out, st = align_clauses(md_text, blocks)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        logger.warning("条文号对齐失败（不影响锚定，块将不带条号）: %s", e)
        n = len(blocks)
        return ([{**b, "clause_no": None} for b in blocks],
                {"clause_blocks": n, "clause_labeled": 0,
                 "clause_unlabeled": n, "clause_distinct": 0})
    # 成功分支同样统一为 `clause_` 前缀：让两个分支产出**同构**的四个键，
    # 调用方（审查页 / 测试）只需认一套键名，也不与锚定 stats 的原生键名混淆。
    return out, {"clause_blocks": st["blocks"], "clause_labeled": st["labeled"],
                 "clause_unlabeled": st["unlabeled"],
                 "clause_distinct": st["distinct_clauses"]}
