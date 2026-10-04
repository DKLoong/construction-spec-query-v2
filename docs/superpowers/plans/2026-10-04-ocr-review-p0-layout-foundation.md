# OCR 审查页改版 · P0 地基 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OCR 的版面信息（逐块的标签/内容/坐标/阅读顺序）落到 `layout.json`，并建立「版面块 → `md_text`」的**可验证**拼装规则。

**Architecture:** 新增 `app/ocr/layout.py` —— 纯函数 + 文件 IO，不碰 OCR 客户端、不碰数据库。它从 OCR 响应的 `prunedResult.parsing_res_list` **白名单式**提取精简 layout（白名单天然剔除签名 URL），并提供反向拼装 `assemble_markdown`。`paddle_api._download_markdown` 在原有取 markdown 的同时**旁路**落盘 `layout.json`（失败只记 WARN，不中断导入）。P0 的出口判据是**往返对拍**：`拼装(原 blocks) ≡ 原 md_text`。

**Tech Stack:** Python 3.14 / FastAPI / pytest / PyMuPDF(`fitz`) / PaddleOCR-VL V2 API

**Spec:** `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`

---

## Global Constraints

以下约束对本计划**每个 Task** 都生效，逐条照抄自 spec：

1. **`md_text` 对外契约不变** —— `parse_markdown` / `plain_text` / `build_embed_text` **零改动**（spec §17）。
2. **不新增依赖** —— 只用已在用的 PyMuPDF（spec §17）。
3. **测试与脚本绝不写入真实 `data/spec_query.db` / `data/uploads` / `data/outputs` / `lance_db`**；需要写盘一律用 `tmp_path` 或 `data/_ocr_reference/`。
4. **落盘的任何 OCR 派生物不得含签名凭据** —— 不得出现 `authorization=`，不得出现 `inputImage` / `preprocessedImages` / `outputImages`（spec §5.2）。
5. **P0 的往返对拍不含任何规范化** —— 表格居中规范化等一律推迟到 P2，且**独立成步**（spec §10.3）。
6. 每个 Task 结束跑**增量测试** + `pyright`，**不得新增 error**，再提交。
7. **提交时逐个 `git add <文件>`，不得用 `git add .`**（工作区可能有他人改动）。
8. 参考夹具放 `data/_ocr_reference/`；`data/` 已在 `.gitignore:11`，**夹具不入库**。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/ocr/layout.py`（新建） | layout 的**提取 / 脱敏 / 落盘 / 读取 / 反向拼装**。纯函数 + 文件 IO，无 OCR 客户端依赖、无 DB 依赖 |
| `scripts/capture_ocr_reference.py`（新建） | **一次性**抓参考夹具（**会调外部付费 API**，仅手动运行） |
| `scripts/verify_layout_roundtrip.py`（新建） | P0 门禁的**验收脚本**：对真实产物逐份跑往返对拍并打印结果 |
| `tests/test_ocr_layout.py`（新建） | 提取 / 脱敏 / 落盘读取 |
| `tests/test_layout_assembly.py`（新建） | 拼装规则 + **往返对拍** |
| `app/ocr/paddle_api.py`（改 `_download_markdown`） | 旁路落盘 layout.json（失败不中断导入） |

**为什么放 `app/ocr/` 而不是 `app/parser/`**：本模块的输入是 **OCR 响应结构**（`parsing_res_list`），不是通用 markdown；且 `md_parser.py` 是全仓最承重的组件（115 个调用方 / 120 个用例 / 正文守恒测试），新文件放它旁边会误导读者以为同族、改动可及（spec §2.2 明确 parser 零改动）。

---

### Task 1: 参考夹具（**会调用一次外部 OCR API**）

> ⚠️ **本 Task 是唯一会花钱的一步。** 运行前须确认可以调用；产出落到 `data/`（已 gitignore），**不入库**。
>
> 为什么必须有夹具：spec §3.3 只实证了**一页三个块**、且只有 `paragraph_title → "## "` 一条前缀映射，其余 label 的映射**未实测**（R7）。拼装规则是本次改版的地基，**不能靠推测**。

**Files:**
- Create: `scripts/capture_ocr_reference.py`
- Create（运行产物，不入库）: `data/_ocr_reference/raw.jsonl`、`data/_ocr_reference/reference.md`

**Interfaces:**
- Consumes: `app.ocr.paddle_api.create_ocr_client()`、`PaddleVLClient._submit_task` / `_poll_result`
- Produces: `data/_ocr_reference/raw.jsonl`（原始 JSONL，**含签名 URL，仅本地**）、`data/_ocr_reference/reference.md`（同一批页面的 markdown，作为对拍的**基准真相**）

- [ ] **Step 1: 写抓取脚本**

```python
"""一次性（可重跑）抓取 P0 参考夹具：原始 OCR JSONL + 同批页面的 markdown。

⚠️ 会调用**外部付费 OCR API**。仅在需要建立/刷新夹具时手动运行：

    D:/Python/python.exe scripts/capture_ocr_reference.py <pdf路径> --pages 1,2,5,6,8,10

产出（`data/` 已 gitignore ⇒ **夹具不入库**，与既有「JGJ107 语料缺则跳过」同一约定）：
    data/_ocr_reference/raw.jsonl      原始 JSONL（含签名 URL，仅本地，勿外传）
    data/_ocr_reference/reference.md   同批页面的 markdown（对拍基准）
"""
import argparse
import asyncio
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import fitz  # noqa: E402

from app.ocr.paddle_api import PaddleVLClient, create_ocr_client  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "_ocr_reference"


def _slice_pages(src: pathlib.Path, pages: list[int]) -> pathlib.Path:
    """把指定页（1-based）抽成一个多页 PDF，避免为夹具跑整本。"""
    doc = fitz.open(str(src))
    out = fitz.open()
    for p in pages:
        if not 1 <= p <= len(doc):
            raise SystemExit(f"页号越界：{p}（该 PDF 共 {len(doc)} 页）")
        out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
    tmp = OUT_DIR / "_slice.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(tmp))
    out.close()
    doc.close()
    return tmp


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="1,2,5,6,8,10",
                    help="1-based 页号，逗号分隔；默认覆盖封面+正文+表/图/公式区")
    args = ap.parse_args()

    src = pathlib.Path(args.pdf)
    pages = [int(x) for x in args.pages.split(",") if x.strip()]

    client = create_ocr_client()
    if not isinstance(client, PaddleVLClient):
        raise SystemExit(f"当前 OCR 后端不是 PaddleOCR-VL：{type(client).__name__}")

    sliced = _slice_pages(src, pages)
    print(f"来源 {src.name} 取第 {pages} 页（共 {len(pages)} 页），已切片")

    job = await client._submit_task(str(sliced))
    print(f"job_id = {job}（等待中…，会调外部 API）")
    data = await client._poll_result(job)
    json_url = (data.get("resultUrl") or {}).get("jsonUrl", "")
    if not json_url:
        raise SystemExit("OCR 结果缺少 resultUrl.jsonUrl")

    import httpx
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.get(json_url)
        resp.raise_for_status()
        raw = resp.text

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "raw.jsonl").write_text(raw, encoding="utf-8")

    # 基准 markdown：用**现有生产规则**（逐行 layoutParsingResults[].markdown.text 拼接），
    # 不引入新规则 —— 它是"真相"，必须来自现状而非我们的新逻辑。
    parts = []
    for line in raw.strip().split("\n"):
        if not line.strip():
            continue
        rec = json.loads(line)
        for lp in (rec.get("result") or {}).get("layoutParsingResults") or []:
            text = (lp.get("markdown") or {}).get("text")
            if text:
                parts.append(text)
    (OUT_DIR / "reference.md").write_text("\n".join(parts), encoding="utf-8")

    print(f"✅ 夹具已写：{OUT_DIR}/raw.jsonl（{len(raw) // 1024} KB）"
          f"、reference.md（{len(''.join(parts))} 字符）")
    sliced.unlink(missing_ok=True)


asyncio.run(main())
```

- [ ] **Step 2: 确认夹具目录不会被提交**

Run:
```bash
git check-ignore -v data/_ocr_reference/raw.jsonl
```
Expected: 输出含 `.gitignore:11:data/`（被忽略）。**若没有输出，停下来报告** —— 夹具入库会带来版权与凭据风险。

- [ ] **Step 3: 运行抓取（一次外部调用）**

Run:
```bash
export PYTHONUTF8=1 && D:/Python/python.exe scripts/capture_ocr_reference.py "data/uploads/8376e02c.pdf" --pages 1,2,5,6,8,10
```
Expected: 打印 job_id、等待、`✅ 夹具已写`。耗时以 OCR 排队为准。

> 若这 6 页里没覆盖到 `table` / `figure` / `formula` 等标签，**换页号重跑**（每次仍是一次外部调用）。覆盖度在 Task 3 Step 2 会打印出来。

- [ ] **Step 4: 核对夹具不含凭据、也不含我们自己的产物**

Run:
```bash
grep -c "authorization=" data/_ocr_reference/raw.jsonl
grep -o '"block_label": "[a-z_]*"' data/_ocr_reference/raw.jsonl | sort | uniq -c
```
Expected: 第一条 **≥1**（原始响应本来就带签名 URL，说明夹具是真响应）；第二条打印出实际覆盖到的标签分布 —— **记录下来**，Task 3 要用。

- [ ] **Step 5: 提交脚本**

```bash
git add scripts/capture_ocr_reference.py
git commit -m "feat: 抓取 P0 参考夹具的脚本（手动运行，会调外部 OCR）"
```

---

### Task 2: layout 的提取 / 脱敏 / 落盘 / 读取

**Files:**
- Create: `app/ocr/layout.py`
- Test: `tests/test_ocr_layout.py`

**Interfaces:**
- Consumes: Task 1 的 `data/_ocr_reference/raw.jsonl`
- Produces:
  - `extract_layout(records: list[dict]) -> dict` → `{"version": int, "pages": [{"index","width","height"}], "blocks": [{"label","content","page","bbox","order","global_block_id"}]}`
  - `save_layout(layout: dict, output_dir: str | Path) -> str`（原子写，返回路径）
  - `load_layout(output_dir: str | Path) -> dict | None`（缺失/损坏一律 `None`）
  - 常量：`LAYOUT_VERSION=1`、`LAYOUT_FILENAME="layout.json"`、`IGNORE_LABELS`、`SIGNED_URL_KEYS`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_ocr_layout.py`：

```python
"""layout 提取 / 脱敏 / 落盘（spec §5）。

夹具在 data/_ocr_reference/ 下 —— `data/` 已 gitignore，故**夹具不入库**
（与既有「JGJ107 语料缺则跳过」同一约定）。缺夹具时**跳过**，不静默空跑。
"""
import json
from pathlib import Path

import pytest

from app.ocr.layout import (LAYOUT_FILENAME, SIGNED_URL_KEYS, extract_layout,
                            load_layout, save_layout)

REPO = Path(__file__).resolve().parent.parent
REF_RAW = REPO / "data" / "_ocr_reference" / "raw.jsonl"

needs_ref = pytest.mark.skipif(
    not REF_RAW.is_file(),
    reason="缺少参考夹具 data/_ocr_reference/raw.jsonl（见 scripts/capture_ocr_reference.py）",
)


def _records() -> list[dict]:
    text = REF_RAW.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in text.split("\n") if ln.strip()]


@needs_ref
def test_extract_layout_shape():
    """提取出 pages 与 blocks，字段齐全（一页一元素 ⇒ index 从 1 起）"""
    layout = extract_layout(_records())
    assert layout["version"] == 1
    assert layout["pages"], "未提取到任何页"
    assert layout["blocks"], "未提取到任何块"
    for p in layout["pages"]:
        assert p["index"] >= 1
        assert p["width"] and p["height"]
    for b in layout["blocks"]:
        assert b["label"], "块缺 label"
        assert isinstance(b["content"], str)
        assert b["page"] >= 1
        assert isinstance(b["bbox"], list) and len(b["bbox"]) == 4


@needs_ref
def test_extract_layout_drops_signed_urls():
    """⚠️ 脱敏硬约束（spec §5.2）：落盘结构里不得出现任何签名凭据。

    判据取**原始响应**与提取结果对比 —— 用「原始里出现过、提取结果里不出现」
    证明剔除真的发生，而不是两边都没有（那样本用例是空转）。
    """
    raw = REF_RAW.read_text(encoding="utf-8")
    assert "authorization=" in raw, "夹具不含签名 URL，本用例无法证明脱敏（请换夹具）"

    dumped = json.dumps(extract_layout(_records()), ensure_ascii=False)
    assert "authorization=" not in dumped
    for key in SIGNED_URL_KEYS:
        assert key not in dumped


def test_save_then_load_roundtrip(tmp_path):
    """落盘再读回内容一致，且文件名固定"""
    layout = {"version": 1,
              "pages": [{"index": 1, "width": 10, "height": 20}],
              "blocks": [{"label": "content", "content": "x", "page": 1,
                          "bbox": [0, 0, 1, 1], "order": 1, "global_block_id": 0}]}
    path = save_layout(layout, tmp_path)
    assert Path(path).name == LAYOUT_FILENAME
    assert load_layout(tmp_path) == layout


def test_load_layout_missing_returns_none(tmp_path):
    """缺文件返回 None（调用方据此降级，spec §13）"""
    assert load_layout(tmp_path) is None


def test_load_layout_corrupt_returns_none(tmp_path):
    """损坏 json 返回 None 且不抛 —— 审查页不得因夹具损坏而 500"""
    (tmp_path / LAYOUT_FILENAME).write_text("{ not json", encoding="utf-8")
    assert load_layout(tmp_path) is None


def test_save_layout_leaves_no_tmp_file(tmp_path):
    """原子写：成功后不留 .tmp 残留"""
    save_layout({"version": 1, "pages": [], "blocks": []}, tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.ocr.layout'`

- [ ] **Step 3: 写最小实现**

创建 `app/ocr/layout.py`：

```python
"""OCR 版面块（layout）的提取、脱敏、落盘与读取。

设计依据：`docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`
§3.2 / §3.3 / §5 / §10.1。

本模块**纯函数 + 文件 IO**：不依赖 OCR 客户端、不碰数据库。因此既可单测，
将来也能让前端通过一个轻端点复用同一份拼装规则（spec §10.1「规则只写一份」）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
LAYOUT_FILENAME = "layout.json"

# 与 OCR 请求参数 `markdownIgnoreLabels` 对齐：这些块**不进 markdown**。
# 实测依据：参考夹具里的 `number` 块（"第5页"）出现在 parsing_res_list，
# 但**不出现**在同页的 markdown.text 中。
IGNORE_LABELS = frozenset({
    "header", "header_image", "footer", "footer_image",
    "number", "footnote", "aside_text",
})

# 官网响应里这些字段是**带凭据的签名 URL**（形如 ?authorization=bce-auth-v1%2F...）。
# 落盘会写进磁盘/备份/分发包，故一律不提取（spec §5.2）。
# 本模块用**白名单式**提取：只取下面显式列出的字段，天然不会带上它们；
# 这个元组仅供测试断言「确实没出现」。
SIGNED_URL_KEYS = ("inputImage", "preprocessedImages", "outputImages")


def extract_layout(records: list[dict]) -> dict:
    """OCR JSONL 记录 → 精简 layout 结构（**已脱敏**）。

    只读 `prunedResult.parsing_res_list`（逐块：标签/内容/坐标/阅读顺序）与
    `prunedResult.width|height`。`markdown`（只有 text/images）不参与 ——
    它可由块反拼装得出，见 `assemble_markdown`。

    **白名单式**：输出结构里每个字段都是显式构造的，因此响应中的签名 URL
    不可能被带进来。
    """
    pages: list[dict] = []
    blocks: list[dict] = []

    for page_idx, rec in enumerate(records, start=1):
        results = (rec.get("result") or {}).get("layoutParsingResults") or []
        for lp in results:
            pr = lp.get("prunedResult") or {}
            pages.append({
                "index": page_idx,
                "width": pr.get("width"),
                "height": pr.get("height"),
            })
            for b in pr.get("parsing_res_list") or []:
                blocks.append({
                    "label": b.get("block_label") or "",
                    "content": b.get("block_content") or "",
                    "page": page_idx,
                    "bbox": b.get("block_bbox"),
                    "order": b.get("block_order"),
                    "global_block_id": b.get("global_block_id"),
                })

    return {"version": LAYOUT_VERSION, "pages": pages, "blocks": blocks}


def save_layout(layout: dict, output_dir: str | Path) -> str:
    """落盘 `layout.json`（原子写：先写 .tmp 再 replace），返回文件路径。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / LAYOUT_FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def load_layout(output_dir: str | Path) -> dict | None:
    """读 `layout.json`；**缺失或损坏一律返回 None**（调用方据此降级，spec §13）。

    这里刻意不抛：审查页没有 layout 时应退化为纯预览，而不是 500。
    """
    path = Path(output_dir) / LAYOUT_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("layout.json 读取失败（按缺失处理）: %s", path)
        return None
    return data if isinstance(data, dict) else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: PASS（若夹具缺失则 2 个 `needs_ref` 用例 SKIPPED —— 其余 4 个必须 PASS）

- [ ] **Step 5: pyright + 提交**

```bash
D:/Python/python.exe -m pyright app/ocr/layout.py tests/test_ocr_layout.py
git add app/ocr/layout.py tests/test_ocr_layout.py
git commit -m "feat: OCR layout 的提取、脱敏、落盘与读取"
```

---

### Task 3: 拼装规则 + 往返对拍（**P0 门禁**）

> 本 Task 的 Step 2 就是 spec R7 的落地：**由夹具实测**得出完整的 label→前缀映射，而不是推测。

**Files:**
- Modify: `app/ocr/layout.py`（加 `LABEL_PREFIX` 常量、`_sorted_blocks`、`assemble_markdown`）
- Create: `tests/test_layout_assembly.py`

**Interfaces:**
- Consumes: Task 2 的 `extract_layout`；Task 1 的 `raw.jsonl` 与 `reference.md`
- Produces: `assemble_markdown(blocks: list[dict]) -> str`；常量 `LABEL_PREFIX: dict[str, str]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_layout_assembly.py`：

```python
"""拼装规则 + 往返对拍（spec §10.1 / §10.3）—— **P0 的硬门禁**。"""
import json
from pathlib import Path

import pytest

from app.ocr.layout import IGNORE_LABELS, assemble_markdown, extract_layout

REPO = Path(__file__).resolve().parent.parent
REF_DIR = REPO / "data" / "_ocr_reference"

needs_ref = pytest.mark.skipif(
    not (REF_DIR / "raw.jsonl").is_file() or not (REF_DIR / "reference.md").is_file(),
    reason="缺少参考夹具 data/_ocr_reference/{raw.jsonl,reference.md}",
)


def normalize(md: str) -> str:
    """往返对拍的归一化判据（spec §10.2）。

    实测差异只在**行尾空白**（`block_content` 的 `.....4␣␣` vs `markdown.text` 的
    `.....4␣␣␣␣`），故只做「去行尾空白 + 去尾部空行」。

    ⚠️ **不要再放宽**：归一化越宽，对拍越失去证明力，而它是本次改版唯一的地基。
    """
    lines = [ln.rstrip() for ln in md.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _first_diff(a: str, b: str) -> str:
    """定位首个差异行并给上下文 —— 失败信息必须能看出**缺了哪个前缀**。"""
    la, lb = a.split("\n"), b.split("\n")
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<缺行>"
        y = lb[i] if i < len(lb) else "<缺行>"
        if x != y:
            lo = max(0, i - 3)
            hi = min(i + 3, max(len(la), len(lb)))
            ctx = "\n".join(f"  [{j}] 拼装={la[j] if j < len(la) else '<缺行>'!r}"
                            f"  参考={lb[j] if j < len(lb) else '<缺行>'!r}"
                            for j in range(lo, hi))
            return (f"首个差异在第 {i + 1} 行：\n"
                    f"  拼装={x!r}\n  参考={y!r}\n上下文：\n{ctx}")
    return "无差异（不应走到这里）"


def _ref() -> tuple[list[dict], str]:
    records = [json.loads(ln) for ln in
               (REF_DIR / "raw.jsonl").read_text(encoding="utf-8").split("\n") if ln.strip()]
    expected = (REF_DIR / "reference.md").read_text(encoding="utf-8")
    return records, expected


@needs_ref
def test_assembly_reproduces_reference_markdown():
    """★ P0 门禁：拼装(夹具的 blocks) 必须复现参考 md（按 normalize 判据）"""
    records, expected = _ref()
    got = assemble_markdown(extract_layout(records)["blocks"])
    assert normalize(got) == normalize(expected), _first_diff(normalize(got), normalize(expected))


@needs_ref
def test_ignored_labels_never_reach_markdown():
    """IGNORE_LABELS 的块内容不得进 md（实测：页号块「第5页」不在 markdown.text 里）"""
    records, _ = _ref()
    layout = extract_layout(records)
    ignored = [b["content"].strip() for b in layout["blocks"]
               if b["label"] in IGNORE_LABELS and b["content"].strip()]
    if not ignored:
        pytest.skip("夹具里没有可忽略标签的块，本用例无法证明（请换夹具）")
    md = assemble_markdown(layout["blocks"])
    for text in ignored:
        assert text not in md, f"被忽略的 {text!r} 出现在 md 里"


def test_assembly_orders_by_global_block_id_and_prefixes_titles():
    """合成用例：按 global_block_id 排序、跳过 number 块、段落标题带 `## ` 前缀"""
    blocks = [
        {"label": "number", "content": "第5页", "page": 1, "bbox": [0, 0, 1, 1],
         "order": None, "global_block_id": 5},
        {"label": "content", "content": "前言", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 2, "global_block_id": 1},
        {"label": "paragraph_title", "content": "目次", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 1, "global_block_id": 0},
    ]
    assert assemble_markdown(blocks) == "## 目次\n\n前言"
```

- [ ] **Step 2: 跑测试确认失败，并**实测**出完整前缀映射**

Run: `D:/Python/python.exe -m pytest tests/test_layout_assembly.py -v`
Expected: FAIL —— `ImportError: cannot import name 'assemble_markdown'`

**实测映射表**（R7 的落地，不要跳过）：

1. 在 `app/ocr/layout.py` 里先只写下**已实证**的一条：
   ```python
   LABEL_PREFIX: dict[str, str] = {
       "paragraph_title": "## ",
   }
   ```
   并实现 `assemble_markdown`（见 Step 3）。
2. 跑门禁用例，**看 `_first_diff` 报出的首个差异**：
   - 差异形如 `拼装='目次' vs 参考='# 目次'` ⇒ 说明该块该加前缀，把它的 label 补进 `LABEL_PREFIX`
   - 差异是一整块内容缺失/多余 ⇒ 回头检查 `IGNORE_LABELS` 与排序
3. **逐个 label 补齐，直到门禁用例通过为止。** 每补一条都在本 Task 的提交信息里写明是哪条。

> ⚠️ **不得凭推测预先填满映射表**。只写被 `_first_diff` 逼出来的那一条 ——
> 这条纪律正是为了让「映射表」始终等于「实测结论」，而不是等于「我以为」。

- [ ] **Step 3: 写实现**

在 `app/ocr/layout.py` 里**追加**（`LABEL_PREFIX` 按 Step 2 的实测结果填）：

```python
# 版面块标签 → Markdown 前缀。
# ⚠️ 只写**实测**出来的条目（见 Task 3 Step 2）：先按空表/单条跑对拍，
# 由 `_first_diff` 报出的差异逼出每一条。凭推测补表会让对拍失去意义。
LABEL_PREFIX: dict[str, str] = {
    "paragraph_title": "## ",
}


def _sort_key(b: dict) -> int:
    """阅读顺序主键：优先 `global_block_id`，缺失时退化为页内 `order`。

    实测：**被忽略的 `number` 块的 `block_order` 为 null**，故不能单拿 order 排序。
    `global_block_id` 逐块递增，是更可靠的主序。
    """
    gid = b.get("global_block_id")
    if gid is not None:
        return gid
    # 退化路径：页号 * 大常数 + 页内序（保住跨页先后，不至于乱序）
    return (b.get("page") or 0) * 100_000 + (b.get("order") or 0)


def assemble_markdown(blocks: list[dict]) -> str:
    """版面块 → Markdown 文本（`md_text` 的拼装规则，spec §10.1）。

    规则：按阅读顺序取**非 `IGNORE_LABELS`** 的块，写
    `LABEL_PREFIX.get(label, "")` 再拼 `content`，块间以**两个换行**分隔。

    ⚠️ 本函数是 `md_text` 的唯一生产路径之一，必须与 `parse_markdown` 的
    切分前提一致：**编号行独立成行**（拼装产出的文本会原样喂给 parse_markdown）。
    """
    parts: list[str] = []
    for b in sorted(blocks, key=_sort_key):
        if (b.get("label") or "") in IGNORE_LABELS:
            continue
        parts.append(LABEL_PREFIX.get(b.get("label") or "", "") + (b.get("content") or ""))
    return "\n\n".join(parts)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_layout_assembly.py -v`
Expected: PASS（缺夹具时 2 个 `needs_ref` SKIPPED，合成用例必须 PASS）

> ⚠️ **若门禁用例（`test_assembly_reproduces_reference_markdown`）仍不过，且试遍 label 前缀仍不过 —— 停下来报告，不要改 `normalize` 去凑。** 这正是 P0 要拦的情况（spec §18：P0 不通过不得进入 P1）。

- [ ] **Step 5: pyright + 提交**

```bash
D:/Python/python.exe -m pyright app/ocr/layout.py tests/test_layout_assembly.py
git add app/ocr/layout.py tests/test_layout_assembly.py
git commit -m "feat: 版面块→markdown 拼装规则与往返对拍（P0 门禁）"
```

---

### Task 4: 接入导入流程 + P0 验收脚本

**Files:**
- Modify: `app/ocr/layout.py`（加 `save_layout_safely`）
- Modify: `app/ocr/paddle_api.py::_download_markdown`（约 380-408 行）
- Create: `scripts/verify_layout_roundtrip.py`
- Test: `tests/test_ocr_layout.py`（追加用例）

**Interfaces:**
- Consumes: Task 2 的 `extract_layout` / `save_layout`；Task 3 的 `assemble_markdown`
- Produces: `save_layout_safely(records: list[dict], output_dir: str | None) -> None`（**永不抛出**）

- [ ] **Step 1: 写失败测试**

在 `tests/test_ocr_layout.py` **追加**：

```python
def test_save_layout_safely_swallows_errors(monkeypatch, tmp_path):
    """落盘失败**不得抛出**（spec §5.3：layout 是可选增强，不该毁掉导入）"""
    import app.ocr.layout as mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "save_layout", boom)
    mod.save_layout_safely([], str(tmp_path))   # 不抛即通过


def test_save_layout_safely_noop_without_output_dir():
    """没有 output_dir 时不做事、不抛"""
    import app.ocr.layout as mod
    mod.save_layout_safely([], None)


def test_save_layout_safely_writes_when_possible(tmp_path):
    """正常路径确实写出 layout.json"""
    import app.ocr.layout as mod
    mod.save_layout_safely([{"result": {"layoutParsingResults": [
        {"prunedResult": {"width": 10, "height": 20, "parsing_res_list": [
            {"block_label": "content", "block_content": "x", "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": 0}]}}]}}], str(tmp_path))
    assert mod.load_layout(tmp_path)["blocks"][0]["content"] == "x"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v -k safely`
Expected: FAIL —— `AttributeError: module 'app.ocr.layout' has no attribute 'save_layout_safely'`

- [ ] **Step 3: 实现 `save_layout_safely`**

在 `app/ocr/layout.py` **追加**：

```python
def save_layout_safely(records: list[dict], output_dir: str | None) -> None:
    """提取并落盘 layout.json；**任何失败都只记 WARN、绝不抛出**（spec §5.3）。

    layout 是审查页的**可选增强**：没有它，审查页退化为纯预览（spec §13）。
    因此它的落盘失败绝不该中断导入 —— 那会为了一个附属功能丢掉主流程。
    """
    if not output_dir:
        return
    try:
        save_layout(extract_layout(records), output_dir)
    except Exception as e:  # 兜底：附属功能失败不得影响主流程
        logger.warning("layout.json 落盘失败（不影响导入）: %s", e)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: PASS（夹具用例可能 SKIPPED）

- [ ] **Step 5: 接入 `_download_markdown`**

先读当前实现（`app/ocr/paddle_api.py` 约 380-408 行）。它逐行解析 JSONL、拼 `parts`、下载图片。**改为先物化 records，再在循环后旁路落盘**：

```python
    async def _download_markdown(self, url: str, output_dir: str | None = None) -> str:
        """从 jsonUrl 下载 JSONL，逐页拼接 markdown 文本，并下载 markdown.images 中的图片

        （原有 docstring 保留）
        """
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        records: list[dict] = []
        parts = []
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(record)
            for lp in record.get("result", {}).get("layoutParsingResults", []):
                md = lp.get("markdown", {})
                if md.get("text"):
                    parts.append(md["text"])
                if output_dir and isinstance(md.get("images"), dict):
                    for img_rel, img_url in md["images"].items():
                        await self._save_image(img_rel, img_url, output_dir)

        # 旁路落盘版面信息（审查页用）。失败只记 WARN，不中断导入（spec §5.3）。
        save_layout_safely(records, output_dir)

        return "\n".join(parts)
```

并在文件顶部的 import 区加：

```python
from app.ocr.layout import save_layout_safely
```

- [ ] **Step 6: 跑相关增量测试**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py tests/test_layout_assembly.py tests/test_paddle_vl_v2.py tests/test_import.py -v`
Expected: 全 PASS（`test_paddle_vl_v2.py` 覆盖 `_download_markdown` 的既有行为，**必须不被破坏**）

- [ ] **Step 7: 写验收脚本**

创建 `scripts/verify_layout_roundtrip.py`：

```python
"""P0 门禁验收：对真实产物逐份跑「拼装(blocks) ≡ 原 md」。

用法：
    D:/Python/python.exe scripts/verify_layout_roundtrip.py

扫描 data/outputs/*/ 下**成对**存在 `<name>.md` 与 `layout.json` 的目录。
全部通过退出码 0；任一不符退出码 1（CI/人工据此判定 P0 是否通过）。
"""
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.ocr.layout import assemble_markdown, load_layout  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUTPUTS = REPO / "data" / "outputs"


def normalize(md: str) -> str:
    """与 tests/test_layout_assembly.py 同一判据（去行尾空白 + 去尾部空行）"""
    lines = [ln.rstrip() for ln in md.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def main() -> int:
    checked = failed = 0
    for d in sorted(OUTPUTS.glob("*/")):
        layout = load_layout(d)
        if layout is None:
            continue
        mds = [p for p in d.glob("*.md")]
        if len(mds) != 1:
            print(f"跳过 {d.name}：期望恰好 1 个 .md，实有 {len(mds)}")
            continue
        expected = mds[0].read_text(encoding="utf-8")
        got = assemble_markdown(layout["blocks"])
        checked += 1
        if normalize(got) == normalize(expected):
            print(f"✅ {d.name}")
        else:
            failed += 1
            ga, ea = normalize(got).split("\n"), normalize(expected).split("\n")
            i = next((i for i in range(max(len(ga), len(ea)))
                      if (ga[i] if i < len(ga) else None) != (ea[i] if i < len(ea) else None)), 0)
            print(f"❌ {d.name}  首个差异 L{i + 1}："
                  f"拼装={ga[i] if i < len(ga) else '<缺行>'!r} "
                  f"参考={ea[i] if i < len(ea) else '<缺行>'!r}")

    print(f"\n共检查 {checked} 份，失败 {failed} 份")
    if checked == 0:
        print("⚠️ 没有可校验的成对产物 —— 需先跑一次真实导入产生 layout.json")
    return 1 if failed else 0


sys.exit(main())
```

- [ ] **Step 8: 跑验收**

Run: `export PYTHONUTF8=1 && D:/Python/python.exe scripts/verify_layout_roundtrip.py`
Expected: 首次运行打印 `共检查 0 份` + 那句 `⚠️ 没有可校验的成对产物`。
**这是预期的**（`layout.json` 尚未由真实导入产生）。**P0 的最终验收 = 让用户跑一次真实导入后再跑本脚本，应当全 ✅。**

- [ ] **Step 9: pyright + 提交**

```bash
D:/Python/python.exe -m pyright app/ocr/layout.py app/ocr/paddle_api.py scripts/verify_layout_roundtrip.py tests/test_ocr_layout.py
git add app/ocr/layout.py app/ocr/paddle_api.py scripts/verify_layout_roundtrip.py tests/test_ocr_layout.py
git commit -m "feat: 导入时旁路落盘 layout.json，并加 P0 往返对拍验收脚本"
```

---

## P0 出口判据（全部满足才算通过，spec §18）

- [ ] 参考夹具跑通：`pytest tests/test_layout_assembly.py` 的 `test_assembly_reproduces_reference_markdown` **PASS**
- [ ] `LABEL_PREFIX` 每一条都是**被 `_first_diff` 逼出来的**，不是推测
- [ ] 落盘结构**不含**任何签名凭据（`test_extract_layout_drops_signed_urls` PASS）
- [ ] `paddle_api` 既有测试未被破坏（`test_paddle_vl_v2.py` PASS）
- [ ] 跑一次**真实导入**后，`scripts/verify_layout_roundtrip.py` 对真实产物**全 ✅**
- [ ] 增量测试 + `pyright` 无新增 error

> **任一条不满足 ⇒ 不得进入 P1**（spec §18：拼装规则一旦有偏差，P2 之后所有编辑都会以「静默改写 `md_text`」的形式污染导入结果）。
