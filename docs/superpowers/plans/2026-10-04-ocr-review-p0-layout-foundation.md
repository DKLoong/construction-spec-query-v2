# OCR 审查页改版 · P0 地基 实施计划（**span 方案 · v3**）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OCR 的版面块与**它自己产出的那篇 markdown** 建立**可验证的行区间对应**，使「只改被编辑的块、其余逐字不变」成为**结构上恒真**的性质。

**Architecture:** 真源是 **API 原始 `markdown.text`（未清洗）**，**不重建**。`app/ocr/layout.py` 做三件事：(a) 从 `prunedResult.parsing_res_list` **白名单式**提取块（天然剔除签名 URL）；(b) 把每个块**锚定**到基准文本的**行区间**（含被剥掉的 `#` 前缀），并做**唯一性校验**；(c) `splice` 按区间拼回 —— **未编辑区间原样取回原行**，故不编辑即逐字节相等。

**Tech Stack:** Python 3.14 / FastAPI / pytest / PyMuPDF(`fitz`) / PaddleOCR-VL V2 API

**Spec:** `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`

> **版本沿革**：v1「反推拼装规则（前缀表）+ 往返对拍」被 Outside Voice 判为反推未公开渲染器 → 废弃。v2「span + 锚定在 `clean_ocr_text` 之后」被第二轮外部复核 + **真机实测**推翻。**v3 的关键结论全部来自 2026-10-04 的两次真实 API 实测**（6 页子集 + 完整 57 页），依据见文末 Decision ledger **R3 / R4**。

---

## 0. 实测事实（**本计划的一切设计都建立在这张表上**）

2026-10-04 用**当前生效参数**真调两次 PaddleOCR-VL（`GBT50080普通混凝土拌合物性能试验方法标准.pdf`，6 页子集 + 完整 57 页）：

| 事实 | 实测值 | 推翻了什么 / 确立了什么 |
| --- | --- | --- |
| 记录数 vs 页数 | **19 records / 57 页** | 一条 record **可含多页** ⇒ 页码必须**扁平累计** |
| `global_block_id` | **全序单调**（各页最小 `0,6,11,13,16,18,21,23,28,48…`） | **文档级全局值**，不是页内局部值 ⇒ 按 gid 排序成立 |
| `clean_ocr_text` 影响 | **删 0 行 / 0 字符**（两次实测一致） | `markdownIgnoreLabels` 已把页眉/页脚/页码排除在 `markdown.text` 之外 ⇒ **清洗无事可做** |
| 标签词表 | `text`477 · `paragraph_title`47 · `number`57 · `content`4 · `display_formula`21 · `formula_number`21 · `image`9 · `figure_title`9 · `vision_footnote`3 · `table`1 · `chart`1 | **词表不可硬编码假设**：同一后端两次跑出 `content` 与 `text` 两种正文标签 |
| **锚定归一化** | 旧规则（仅去行尾空白 + 首行 `#`）：**541/593 命中，52 未命中** | 缺一条关键规则 → 见下 |
| **关键缺口** | `markdown.text` 把块内**每行渲染成独立段落**（行间插空行），`block_content` 里是单 `\n` | 补「**行间容忍空行**」后：**552/593**，未命中 41 |
| **未命中的构成** | 41 = `formula_number` 21 + `image` 9（**内容为空、本就不进 markdown**）+ **残留 11** | 门禁**不能一刀切** `missed==0`；须**分类豁免 + 残留逐条有解释** |
| **唯一性** | 6 页子集 0 歧义；**完整 57 页 15 个歧义候选** | 子集样本太小 ⇒ **必须做唯一性校验** |
| **锚定耗时** | 650 块 / 1244 行：旧规则 9 ms，新规则 **68 ms** | 性能**不是瓶颈**；门禁阈值可设 2000 ms |

⚠️ **未实测、不得假设的**：残留 11 个未命中的**成因**（Task 3 Step 5 专门诊断）；`image` 块在**其他文档**是否带 `![](imgs/…)` 引用。

---

## Global Constraints

1. **`md_text` 对外契约不变** —— `parse_markdown` / `plain_text` / `build_embed_text` **零改动**（spec §17）。
2. **不新增依赖** —— 只用已在用的 PyMuPDF。
3. **测试与脚本绝不写入真实 `data/spec_query.db` / `data/uploads` / `data/outputs` / `lance_db`**；写盘一律 `tmp_path` 或 `data/_ocr_reference/`（不入库）。
4. **落盘的任何 OCR 派生物不得含签名凭据** —— 不得出现 `authorization=`。**落盘前脱敏**（不是"靠 .gitignore"）。
5. **门禁必须 fail-closed** —— "夹具缺失 / 检查 0 份 / 未设 `REQUIRE_LAYOUT_FIXTURE=1`" 一律**不通过**。
6. 每个 Task 跑**增量测试** + `pyright`，不得新增 error，再提交。
7. **pyright 用 npm 全局二进制**：`pyright <显式路径>`。⚠️ `D:/Python/python.exe -m pyright` 实测报 `No module named pyright`；`pytest` 仍可走 `python -m pytest`。
8. **提交时逐个 `git add <文件>`，不得 `git add .`**。
9. **不写死 OCR 标签词表与参数** —— 生效参数（含 `markdownIgnoreLabels`）**必须记录进 `layout.json`**；标签分类由「实测 + 生效参数」得出，不得凭印象。

## 产物覆盖边界（写清，免得读者以为 layout 普遍存在）

`layout.json` **只在 PaddleOCR-VL 后端 + 扫描件路径产生**：

| 路径 | 有无 layout | 原因 |
| --- | --- | --- |
| `.pdf` + PaddleOCR-VL（扫描件或 `force_ocr`） | ✅ 有 | 走 `PaddleVLClient._download_markdown` |
| `.pdf` + `accurate-basic` / `custom` 后端 | ❌ 无 | 这两个客户端**自己写 md**（`paddle_api.py:99` / `:574`） |
| `.pdf` **非扫描件** | ❌ 无 | `is_scanned()` 为假 ⇒ 走 `extract_text()`，不跑 OCR |
| `.md` 上传 | ❌ 无 | 直读文件（另走源码审查页，spec §20） |

没有 layout 时审查页**降级为纯预览**（spec §13），不报错。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/ocr/layout.py`（新建） | 块**提取/脱敏/锚定/拼接/落盘读取** + **标签分类**。纯函数 + 文件 IO，无 OCR 客户端依赖、无 DB 依赖 |
| `scripts/capture_ocr_reference.py`（新建） | 一次性抓参考夹具（**付费 API**；**落盘前脱敏**） |
| `scripts/verify_layout_anchor.py`（新建） | P0 验收：锚定覆盖率 + 残留解释，**fail-closed** |
| `tests/fixtures/ocr_layout_golden.jsonl`（新建，**入库**） | **合成**黄金夹具（手写假文本、无凭据），让骨架测试在 CI 真跑 |
| `tests/test_ocr_layout.py`（新建） | 提取 / 脱敏 / 落盘读取 / schema |
| `tests/test_layout_anchor.py`（新建） | **锚定 + 拼接 + 唯一性**（核心门禁） |
| `app/ocr/paddle_api.py`（改 `_download_markdown`） | 旁路落**未锚定**的 `layout.raw.json`（失败不中断导入） |
| `app/routes/import_routes.py`（改 `_process_import`） | 消费 raw，锚定并落 `layout.json` |
| `app/routes/import_routes.py`（改 `.md` 分支 + `review_page`） | **`.md` 走源码审查页**（spec §20）—— 见 Task 5 |

**为什么放 `app/ocr/`**：输入是 **OCR 响应结构**；且 `md_parser.py` 是全仓最承重组件（115 调用方 / 120 用例 / 正文守恒测试），新文件放旁边会误导读者以为同族可改（spec §2.2 明确 parser 零改动）。

---

### Task 1: 参考夹具（**付费 API**）+ 三项复核实测

> ⚠️ 唯一会花钱的一步。运行前确认可调用。
> ℹ️ **已有现成夹具**（2026-10-04 实测产出，**已脱敏**，在 `data/_ocr_reference/`）：`full.raw.jsonl` + `GBT50080…md`（57 页 / 30975 字符）。**可直接使用**；仅当要换文档或刷新时才重跑。

**Files:**
- Create: `scripts/capture_ocr_reference.py`
- Create（运行产物，**不入库**）: `data/_ocr_reference/raw.jsonl`（**脱敏**）、`reference.md`

- [ ] **Step 1: 写抓取脚本（含落盘前脱敏）**

```python
"""一次性（可重跑）抓取 P0 参考夹具：OCR 原始 JSONL（**脱敏后**）+ 同批页面的 markdown。

⚠️ 会调用**外部付费 OCR API**。手动运行：

    D:/Python/python.exe scripts/capture_ocr_reference.py <pdf路径> [--pages 1,2,5]

⚠️ **落盘前必须脱敏**：`inputImage` / `preprocessedImages` / `outputImages` 都是
**带凭据的签名 URL**。`.gitignore` 只防提交，不防磁盘、备份与分发打包 ⇒
在**内存中**完成断言与替换，再写盘。

产出（`data/` 已 gitignore ⇒ 不入库）：
    data/_ocr_reference/raw.jsonl      已脱敏的原始 JSONL
    data/_ocr_reference/reference.md   基准 markdown（**未清洗**，见 §0）
"""
import argparse
import asyncio
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import fitz  # noqa: E402

from app.ocr.paddle_api import PaddleVLClient, create_ocr_client  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "_ocr_reference"

# 空格终止仅排除引号；JSON 里仍是合法的 "<redacted-signed-url>"
_SIGNED_URL = re.compile(r'https?://[^"\s\\]*authorization=[^"\s\\]*')


def _scrub(raw: str) -> tuple[str, int]:
    """把所有签名 URL 替换为占位符，返回 (脱敏文本, 替换次数)。"""
    return _SIGNED_URL.sub("<redacted-signed-url>", raw)


def _slice(src: pathlib.Path, pages: list[int]) -> pathlib.Path:
    """把指定页（1-based）抽成多页 PDF；`pages` 为空则用整本。"""
    if not pages:
        return src
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
    ap.add_argument("--pages", default="", help="1-based 页号，逗号分隔；留空=整本")
    args = ap.parse_args()

    src = pathlib.Path(args.pdf)
    pages = [int(x) for x in args.pages.split(",") if x.strip()]
    client = create_ocr_client()
    if not isinstance(client, PaddleVLClient):
        raise SystemExit(f"当前 OCR 后端不是 PaddleOCR-VL：{type(client).__name__}")

    target = _slice(src, pages)
    print(f"来源 {src.name}，页数={'整本' if not pages else len(pages)}")

    job = await client._submit_task(str(target))
    print(f"job_id = {job}（等待中…，会调外部 API）")
    data = await client._poll_result(job, max_wait=900)
    json_url = (data.get("resultUrl") or {}).get("jsonUrl", "")
    if not json_url:
        raise SystemExit("OCR 结果缺少 resultUrl.jsonUrl")

    import httpx
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.get(json_url)
        resp.raise_for_status()
        raw = resp.text

    # ① 先在**内存中**证明拿到了真响应（有签名 URL），再脱敏落盘
    n_signed = len(_SIGNED_URL.findall(raw))
    assert n_signed > 0, "响应里没有签名 URL —— 夹具不是真响应，停下来报告"
    scrubbed = _scrub(raw)[0]
    assert "authorization=" not in scrubbed, "脱敏不彻底"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "raw.jsonl").write_text(scrubbed, encoding="utf-8")

    # ② 基准 markdown：用**现有生产规则**拼接（逐行 layoutParsingResults[].markdown.text）
    parts = []
    for line in scrubbed.strip().split("\n"):
        if not line.strip():
            continue
        for lp in (json.loads(line).get("result") or {}).get("layoutParsingResults") or []:
            t = (lp.get("markdown") or {}).get("text")
            if t:
                parts.append(t)
    reference = "\n".join(parts)
    (OUT_DIR / "reference.md").write_text(reference, encoding="utf-8")

    print(f"✅ 已脱敏 {n_signed} 处签名 URL；reference.md = {len(reference)} 字符")
    if not pages:
        (OUT_DIR / f"{src.stem}.md").write_text(reference, encoding="utf-8")
        print(f"✅ 另存一份可直接导入的 md：{OUT_DIR / (src.stem + '.md')}")


asyncio.run(main())
```

- [ ] **Step 2: 确认夹具目录不会被提交**

Run: `git check-ignore -v data/_ocr_reference/raw.jsonl`
Expected: 输出含 `.gitignore:11:data/`。**无输出则停下来报告**。

- [ ] **Step 3: 抓取**

若用现成夹具，跳过本步。否则：
```bash
export PYTHONUTF8=1 && D:/Python/python.exe scripts/capture_ocr_reference.py "D:/1赖啸龙/规范及标准/混凝土试验/GBT50080普通混凝土拌合物性能试验方法标准.pdf"
```

- [ ] **Step 4: 复核三项实测（确认 §0 的表在当前夹具上仍成立）**

Run:
```bash
export PYTHONUTF8=1 && D:/Python/python.exe -c "
import json,pathlib,sys
sys.path.insert(0,'.')
from collections import Counter
from app.ocr.layout import extract_layout, sort_key_stats
recs=[json.loads(l) for l in pathlib.Path('data/_ocr_reference/raw.jsonl').read_text(encoding='utf-8').split(chr(10)) if l.strip()]
lay=extract_layout(recs)
print('页数',len(lay['pages']),'块数',len(lay['blocks']),'记录数',len(recs))
print('标签',dict(Counter(b['label'] for b in lay['blocks'])))
print('gid 统计', sort_key_stats(lay['blocks']))
"
```
Expected: 页数 = record 覆盖的总页数（**应 > 记录数**，证明多页/记录）；标签里**同时**出现 `text` 与/或 `content`；`sort_key_stats` 报 `all_have_gid=True`、`monotonic=True`。
**任一项不符 ⇒ 先更新 §0 的表，再继续**（§0 是设计依据，不许过期）。

- [ ] **Step 5: 提交脚本**

```bash
git add scripts/capture_ocr_reference.py
git commit -m "feat: 抓取 P0 参考夹具的脚本（手动运行；落盘前脱敏）"
```

---

### Task 2: 块的提取 / 脱敏 / 标签分类 / 落盘读取

**Files:**
- Create: `app/ocr/layout.py`
- Test: `tests/test_ocr_layout.py`
- Create（**入库**）: `tests/fixtures/ocr_layout_golden.jsonl`

**Interfaces:**
- Produces:
  - `extract_layout(records) -> {"version", "pages", "blocks": [{"label","content","page","bbox","order","global_block_id"}]}`
  - `sort_key_stats(blocks) -> {"all_have_gid", "monotonic"}`
  - `classify_labels(blocks, ignore_labels) -> {"anchorable": [...], "exempt": [...], "exempt_reasons": {...}}`
  - `save_layout(layout, output_dir) -> str`（原子写）
  - `load_layout(output_dir) -> dict | None`（缺失/**损坏**/**schema 不符**一律 `None`）
  - `save_raw_layout_safely(records, output_dir)`（**永不抛出**）
  - 常量：`LAYOUT_VERSION=1`、`LAYOUT_FILENAME`、`LAYOUT_RAW_FILENAME`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_ocr_layout.py`：

```python
"""layout 提取 / 脱敏 / schema（spec §5）。"""
import json
import os
from pathlib import Path

import pytest

from app.ocr.layout import (LAYOUT_FILENAME, SIGNED_URL_KEYS, classify_labels,
                            extract_layout, load_layout, save_layout, sort_key_stats)

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "ocr_layout_golden.jsonl"
REF = REPO / "data" / "_ocr_reference" / "raw.jsonl"

IGNORE = ["number", "formula_number", "image"]


def _recs(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").split("\n") if l.strip()]


def _require_ref() -> None:
    """⚠️ P0 验收用 `REQUIRE_LAYOUT_FIXTURE=1` 跑；此时缺失是**失败**而非跳过，
    否则门禁 fail-open（pytest 全绿但其实一个真实块都没锚过）。"""
    if REF.is_file():
        return
    if os.environ.get("REQUIRE_LAYOUT_FIXTURE") == "1":
        pytest.fail("缺少真实夹具：P0 验收不得空过（见 scripts/capture_ocr_reference.py）")
    pytest.skip("缺少真实夹具 data/_ocr_reference/raw.jsonl")


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
    assert [p["index"] for p in lay["pages"]] == [1, 2, 3, 4]
    assert [b["page"] for b in lay["blocks"]] == [1, 2, 3, 4]


def test_extract_layout_is_whitelist_so_signed_urls_cannot_survive():
    """★ 脱敏：输出是**白名单构造**的，签名 URL 不可能进来（用合成输入反证，不是空转）"""
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
    rec = {"result": {"layoutParsingResults": [{"prunedResult": {"width": 1, "height": 1,
        "parsing_res_list": ["not-a-dict", None, {"block_label": "text",
            "block_content": "ok", "block_bbox": [0, 0, 1, 1], "block_order": 1,
            "global_block_id": 0}]}}]}}
    blocks = extract_layout([rec])["blocks"]
    assert len(blocks) == 1 and blocks[0]["content"] == "ok"


# ── 排序键 ─────────────────────────────────────────────

def test_sort_key_stats_reports_monotonic_and_missing():
    ok = [{"global_block_id": 0}, {"global_block_id": 1}, {"global_block_id": 2}]
    assert sort_key_stats(ok) == {"all_have_gid": True, "monotonic": True}
    missing = [{"global_block_id": 0}, {"global_block_id": None}]
    assert sort_key_stats(missing)["all_have_gid"] is False


def test_classify_labels_exempts_not_in_markdown_and_empty_content():
    """门禁不能一刀切：豁免类不计入未命中，且**理由可追溯**"""
    blocks = [{"label": "text", "content": "正文", "page": 1, "bbox": [0, 0, 1, 1],
               "order": 1, "global_block_id": 0},
              {"label": "image", "content": "", "page": 1, "bbox": [0, 0, 1, 1],
               "order": 2, "global_block_id": 1},
              {"label": "number", "content": "第1页", "page": 1, "bbox": [0, 0, 1, 1],
               "order": 3, "global_block_id": 2}]
    cls = classify_labels(blocks)
    assert cls["anchorable"] == [0]
    assert sorted(cls["exempt"]) == [1, 2]
    assert "image" in cls["exempt_reasons"][1] and "number" in cls["exempt_reasons"][2]
```

- [ ] **Step 2: 造合成黄金夹具（入库）**

创建 `tests/fixtures/ocr_layout_golden.jsonl`（一行一 record；**手写假文本、无真实规范内容、无凭据**）：

```json
{"result": {"layoutParsingResults": [{"prunedResult": {"width": 100, "height": 200, "parsing_res_list": [{"block_label": "paragraph_title", "block_content": "目次", "block_bbox": [10, 10, 40, 20], "block_order": 1, "global_block_id": 0}, {"block_label": "text", "block_content": "前言.....4\n1 总则.....6", "block_bbox": [10, 30, 90, 60], "block_order": 2, "global_block_id": 1}, {"block_label": "number", "block_content": "第1页", "block_bbox": [40, 180, 60, 190], "block_order": null, "global_block_id": 2}]}}]}}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.ocr.layout'`

- [ ] **Step 4: 写实现**

创建 `app/ocr/layout.py`：

```python
"""OCR 版面块（layout）的提取、脱敏、标签分类、锚定、拼接、落盘与读取。

设计依据：spec `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`
§3.2 / §3.3 / §5 / §10，以及本计划 §0 的**真机实测事实**。

**核心设计（span 方案）**：真源是 OCR 自己产出的 `markdown.text`（**未清洗**），
**不重建**。每个块锚定到基准文本的**行区间**（含被剥掉的 `#` 前缀）；拼接时
**未编辑的区间原样取回原行** ⇒ 「不编辑即逐字节相等」是**结构上恒真**的。

本模块**纯函数 + 文件 IO**：不依赖 OCR 客户端、不碰数据库，故可单测，
将来也能让前端通过一个轻端点复用同一份锚定/拼接规则（spec §10.1）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
LAYOUT_FILENAME = "layout.json"
LAYOUT_RAW_FILENAME = "layout.raw.json"

# 官网响应里这些字段是**带凭据的签名 URL**；本模块白名单式提取，天然不会带上它们。
SIGNED_URL_KEYS = ("inputImage", "preprocessedImages", "outputImages")

# ⚠️ 实测（2026-10-04，完整 57 页）**不进 `markdown.text`** 的标签：
#   - 与请求参数 `markdownIgnoreLabels` 对齐的：header/footer/number/footnote/aside_text…
#   - **实测额外发现**：`formula_number`(21) 与 `image`(9) 的 block_content **也不出现在
#     markdown.text 里**（grep 无命中），而它们**不在** `markdownIgnoreLabels` 里。
# ⇒ 本名单**不是**从参数抄的，而是「参数 ∪ 实测」；仍须在每次刷新夹具时用 Task 1 Step 4 复核。
DEFAULT_NOT_IN_MARKDOWN = frozenset({
    "header", "header_image", "footer", "footer_image",
    "number", "footnote", "aside_text",
    "formula_number", "image",
})

_HASH_PREFIX = re.compile(r"^#{1,6}\s+(.*)$")


def sort_key_stats(blocks: list[dict]) -> dict:
    """报告排序键的可用性（供 Task 1 Step 4 的实测复核用）。"""
    gids = [b.get("global_block_id") for b in blocks]
    all_have = all(g is not None for g in gids)
    return {
        "all_have_gid": all_have,
        "monotonic": all_have and all(gids[i] <= gids[i + 1] for i in range(len(gids) - 1)),
    }


def extract_layout(records: list[dict]) -> dict:
    """OCR JSONL 记录 → 精简 layout 结构（**已脱敏**、**尚未锚定**）。

    ⚠️ 页码是**跨 record 的扁平累计序号**。实测：完整产物是 19 条 record / 57 页
    —— 一条 record 多页是常态，用 record 下标会让第 2..N 页撞号（静默错页）。
    """
    pages: list[dict] = []
    blocks: list[dict] = []

    page_no = 0
    for rec in records:
        for lp in (rec.get("result") or {}).get("layoutParsingResults") or []:
            page_no += 1
            pr = lp.get("prunedResult") or {}
            pages.append({"index": page_no, "width": pr.get("width"),
                          "height": pr.get("height")})
            for b in pr.get("parsing_res_list") or []:
                if not isinstance(b, dict):       # 畸形项不得中断整条链
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
    `formula_number`(21) + `image`(9) —— 它们**本就不在 markdown.text 里**，
    未命中是**正确行为**，不是缺陷。所以必须分类，且豁免理由必须可追溯。

    `ignore_labels` 传**本次生效**的 `markdownIgnoreLabels`（来自 OCR 参数，
    会记进 `layout.json`），未传则只用 `DEFAULT_NOT_IN_MARKDOWN`。
    """
    not_in_md = set(DEFAULT_NOT_IN_MARKDOWN) | set(ignore_labels or [])
    anchorable, exempt, reasons = [], [], {}
    for i, b in enumerate(blocks):
        label = b.get("label") or ""
        content = (b.get("content") or "").strip()
        if label in not_in_md:
            exempt.append(i)
            reasons[i] = f"标签 {label!r} 不进 markdown.text"
        elif not content:
            exempt.append(i)
            reasons[i] = "内容为空"
        else:
            anchorable.append(i)
    return {"anchorable": anchorable, "exempt": exempt, "exempt_reasons": reasons,
            "not_in_markdown": sorted(not_in_md)}


def save_layout(layout: dict, output_dir: str | Path) -> str:
    """落盘（原子写：先写 .tmp 再 replace），返回文件路径。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / LAYOUT_FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def base_sha256(text: str) -> str:
    """基准文本指纹 —— 用来在加载时确认 layout 与文本仍是同一份（复验 #5）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_valid_layout(data: object) -> bool:
    """**严格** schema 校验（复验 #7）。

    只查「version 是 int、pages/blocks 是 list」太松：
    `{"version":99,"pages":[],"blocks":[]}`、`blocks=["x"]`、越界/倒置 span 都能过，
    而 P1 会据此渲染炸掉。这里逐项查类型与区间。
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
            continue                               # 未锚定，合法
        if not (isinstance(s, int) and isinstance(e, int)) or s < 0 or e <= s:
            return False
    return True


def load_layout(output_dir: str | Path) -> dict | None:
    """读 `layout.json`；**缺失 / 损坏 / schema 不符一律 None**（调用方据此降级，spec §13）。

    刻意不抛：审查页没有可用 layout 时退化为纯预览，而不是 500。
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
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: PASS

- [ ] **Step 6: pyright + 提交**

```bash
pyright app/ocr/layout.py tests/test_ocr_layout.py
git add app/ocr/layout.py tests/test_ocr_layout.py tests/fixtures/ocr_layout_golden.jsonl
git commit -m "feat: layout 提取/脱敏/标签分类/schema 校验与落盘读取"
```

---

### Task 3: 锚定（含唯一性校验）与区间拼接（P0 核心）

**Files:**
- Modify: `app/ocr/layout.py`（加 `anchor_blocks` / `splice`）
- Create: `tests/test_layout_anchor.py`

**Interfaces:**
- `anchor_blocks(base_text, blocks, ignore_labels=None) -> (blocks_with_span, stats)`
  - 每块新增 `start_line` / `end_line` / `prefix`（原文被剥掉的 `#` 前缀，可能是 `""`）
  - `stats = {"total", "anchorable", "anchored", "missed", "ambiguous", "exempt", "residual_miss": [...]}`
- `splice(base_text, blocks, edits: dict[int, str]) -> str`；`edits` 键是 `blocks` 下标

- [ ] **Step 1: 写失败测试**

创建 `tests/test_layout_anchor.py`：

```python
"""锚定 + 拼接 + 唯一性（spec §10）—— **P0 的硬门禁**。

核心性质：**不编辑 ⇒ 逐字节相等**。它不是"要对拍证明的命题"，而是**结构上恒真**
（拼接对未编辑区间原样取回原行）。真正要守的是：**锚定覆盖率 + 唯一性 + 前缀保留**。
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


# ── 拼接：核心不变量 ───────────────────────────────────

def test_splice_with_no_edits_is_byte_identical():
    """★ 不编辑 ⇒ **逐字节相等**（本方案的地基，不是"对拍跑出来的"）"""
    base = "A\n\nB\n\nC"
    bl = [{"label": "text", "content": "A", "start_line": 0, "end_line": 1, "prefix": ""},
          {"label": "text", "content": "B", "start_line": 2, "end_line": 3, "prefix": ""},
          {"label": "text", "content": "C", "start_line": 4, "end_line": 5, "prefix": ""}]
    assert splice(base, bl, {}) == base


def test_splice_touches_only_the_edited_block():
    base = "A\n\nB\n\nC"
    bl = [{"label": "text", "content": "A", "start_line": 0, "end_line": 1, "prefix": ""},
          {"label": "text", "content": "B", "start_line": 2, "end_line": 3, "prefix": ""},
          {"label": "text", "content": "C", "start_line": 4, "end_line": 5, "prefix": ""}]
    assert splice(base, bl, {1: "B2"}) == "A\n\nB2\n\nC"
    assert splice(base, bl, {0: "A2", 2: "C2"}) == "A2\n\nB\n\nC2"


def test_splice_preserves_hash_prefix_when_editing_a_title():
    """★ 编辑标题块**必须保留原文的 `#` 前缀**（复验 #4）。

    标题块锚到 `## 目次`，块内容是 `目次`。若 splice 直接写新行，输出会是裸行
    `新目次` —— 直接改变 `parse_markdown` 的**标题型判定**（`#` 前缀行无条件判标题型）。
    """
    base = "## 目次\n\n正文"
    bl = [{"label": "paragraph_title", "content": "目次", "start_line": 0, "end_line": 1,
           "prefix": "## "},
          {"label": "text", "content": "正文", "start_line": 2, "end_line": 3, "prefix": ""}]
    assert splice(base, bl, {0: "新目次"}) == "## 新目次\n\n正文"


def test_splice_raises_on_overlapping_spans():
    """★ 区间重叠是**编程错误**，必须显式失败 —— 静默跳过 = 用户那次编辑被悄悄丢掉"""
    base = "A\n\nB"
    bl = [{"label": "text", "content": "A", "start_line": 0, "end_line": 3, "prefix": ""},
          {"label": "text", "content": "B", "start_line": 2, "end_line": 4, "prefix": ""}]
    with pytest.raises(ValueError):
        splice(base, bl, {})


def test_splice_ignores_unanchored_blocks():
    base = "A\n\nB"
    bl = [{"label": "text", "content": "A", "start_line": 0, "end_line": 1, "prefix": ""},
          {"label": "image", "content": "", "start_line": None, "end_line": None, "prefix": ""},
          {"label": "text", "content": "B", "start_line": 2, "end_line": 3, "prefix": ""}]
    assert splice(base, bl, {}) == base
    assert splice(base, bl, {1: "不该出现"}) == base


def test_splice_multiline_replacement():
    base = "A\n\nB"
    bl = [{"label": "text", "content": "A", "start_line": 0, "end_line": 1, "prefix": ""},
          {"label": "text", "content": "B", "start_line": 2, "end_line": 3, "prefix": ""}]
    assert splice(base, bl, {1: "B1\nB2"}) == "A\n\nB1\nB2"


# ── 锚定 ──────────────────────────────────────────────

def test_anchor_tolerates_blank_lines_between_block_lines():
    """★ **行间空行**（实测逼出的第三条归一化，前两轮外部复核都没发现）。

    实测：`markdown.text` 把 `block_content` 的**每一行渲染成独立段落**——
        block_content:  '甲\\n乙'
        reference.md:   '甲'  /  ''  /  '乙'
    只容忍「连续行」的旧规则 ⇒ 6 页子集 51/60、完整 57 页 541/593（未命中 52）。
    补上本规则后：552/593。
    """
    base = "甲\n\n乙\n\n丙"
    bl = [{"label": "text", "content": "甲\n乙", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 1, "global_block_id": 0}]
    anchored, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 0
    assert anchored[0]["start_line"] == 0 and anchored[0]["end_line"] == 3
    assert splice(base, anchored, {}) == base


def test_anchor_tolerates_hash_prefix_and_trailing_whitespace():
    """另两条归一化：**首行允许原文多一个 `#` 前缀**（记进 `prefix`）+ **去行尾空白**"""
    base = "## 目次\n\n前言.....4    \n1 总则.....6    "
    bl = [{"label": "paragraph_title", "content": "目次", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 1, "global_block_id": 0},
          {"label": "text", "content": "前言.....4\n1 总则.....6", "page": 1,
           "bbox": [0, 0, 1, 1], "order": 2, "global_block_id": 1}]
    anchored, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 0
    assert anchored[0]["prefix"] == "## " and anchored[1]["prefix"] == ""
    assert splice(base, anchored, {}) == base


def test_anchor_records_ambiguity_and_refuses_to_guess():
    """★ **唯一性校验**（复验 #3；完整 57 页实测出现 15 个歧义候选，子集是 0 ——
    样本太小会骗人）。候选 >1 ⇒ 记 `ambiguous` 且**不锚定**（宁缺勿错，不赌第一个）。
    """
    base = "重复行\n\n中间\n\n重复行"
    bl = [{"label": "text", "content": "重复行", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 1, "global_block_id": 0}]
    anchored, stats = anchor_blocks(base, bl)
    assert stats["ambiguous"] == 1
    assert anchored[0]["start_line"] is None


def test_anchor_uses_cursor_to_disambiguate_in_order():
    """有了游标 + 单调推进，**按序出现**的重复内容不该被判歧义"""
    base = "重复\n\n重复"
    bl = [{"label": "text", "content": "重复", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 1, "global_block_id": 0},
          {"label": "text", "content": "重复", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 2, "global_block_id": 1}]
    anchored, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 0 and stats["ambiguous"] == 0
    assert (anchored[0]["start_line"], anchored[1]["start_line"]) == (0, 2)


def test_anchor_exempts_labels_not_in_markdown():
    """豁免类别**不算未命中**（实测：formula_number 21 + image 9 本就不进 markdown）"""
    base = "正文"
    bl = [{"label": "text", "content": "正文", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 1, "global_block_id": 0},
          {"label": "image", "content": "", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 2, "global_block_id": 1},
          {"label": "formula_number", "content": "(7.0.5)", "page": 1,
           "bbox": [0, 0, 1, 1], "order": 3, "global_block_id": 2}]
    _, stats = anchor_blocks(base, bl)
    assert stats["exempt"] == 2 and stats["missed"] == 0 and stats["anchorable"] == 1


def test_anchor_reports_residual_miss_without_raising():
    """残留未命中**不抛**，但必须**逐条可解释**地报出来（供门禁判定）"""
    base = "完全不相干"
    bl = [{"label": "text", "content": "这里没有这块", "page": 1, "bbox": [0, 0, 1, 1],
           "order": 1, "global_block_id": 0}]
    anchored, stats = anchor_blocks(base, bl)
    assert stats["missed"] == 1
    assert stats["residual_miss"] and stats["residual_miss"][0][1] == "text"
    assert anchored[0]["start_line"] is None
    assert splice(base, anchored, {}) == base


def test_anchor_real_fixture_gate():
    """★ **P0 门禁**：真实夹具上
      - `missed`（**排除豁免类**）必须为 0 **或** 每条残留都有解释
      - 未编辑必须**逐字节相等**
    """
    _require_ref()
    base = (REF_DIR / "reference.md").read_text(encoding="utf-8")
    anchored, stats = anchor_blocks(base, _blocks_of(REF_DIR / "raw.jsonl"))

    assert splice(base, anchored, {}) == base, "未编辑必须逐字节相等"
    assert stats["anchorable"] > 0, "可锚定块为 0 ⇒ 夹具或分类有问题"
    assert stats["ambiguous"] == 0, (
        f"有 {stats['ambiguous']} 个歧义候选 ⇒ 锚定不唯一，必须先把归一化/唯一性判据收紧："
        f"{stats['residual_miss'][:5]}")
    # 残留未命中：允许存在，但**必须逐条列出来**（门禁在 Task 4 的验收脚本里判定阈值）
    if stats["missed"]:
        print(f"\n⚠️ 残留未命中 {stats['missed']} 条：{stats['residual_miss'][:10]}")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py -v`
Expected: FAIL —— `ImportError: cannot import name 'anchor_blocks'`

- [ ] **Step 3: 写实现**

在 `app/ocr/layout.py` **追加**：

```python
def _strip_hash_prefix(line: str) -> tuple[str, str]:
    """返回 (去掉 `#` 前缀后的文本, 被剥掉的前缀)。无前缀则前缀为空串。

    例：`"## 目次"` → `("目次", "## ")`；`"正文"` → `("正文", "")`。
    被剥掉的前缀会**记进 span**，供 `splice` 原样保留（复验 #4）。
    """
    m = _HASH_PREFIX.match(line)
    if not m:
        return line.rstrip(), ""
    return m.group(1).rstrip(), line[: m.start(1)]


def _norm(line: str) -> str:
    return line.rstrip()


def _find_candidates(lines: list[str], content: str, start: int,
                     max_gap: int = 2, limit: int = 3) -> list[tuple[int, int, str]]:
    """在 `lines[start:]` 找 `content` 的候选区间（**最多收 `limit` 个**）。

    **三条归一化**（每条都由真机实测逼出，见本计划 §0）：
    1. **去行尾空白** —— `block_content` 是 `.....4␣␣`，`markdown.text` 是 `.....4␣␣␣␣`
    2. **首行允许原文多一个 `#` 前缀** —— 标题型块 content 是 `目次`，原文是 `## 目次`；
       被剥掉的前缀会**记进 span**，供 `splice` 原样保留（复验 #4）
    3. **块内行之间容忍 ≤`max_gap` 个空行** —— 实测：`markdown.text` 把块内每行渲染成
       独立段落；只容忍连续行的旧规则在完整文档上漏掉 52 个块

    返回 `[(start_line, end_line, prefix)]`。**收多个候选是为了让调用方能判唯一性**。
    """
    want = [_norm(x) for x in content.split("\n")]
    while want and not want[-1]:
        want.pop()
    if not want:
        return []

    out: list[tuple[int, int, str]] = []
    n_lines = len(lines)
    for i in range(start, n_lines):
        got = _norm(lines[i])
        stripped, prefix = _strip_hash_prefix(got)
        if not (got == want[0] or (not want[0].startswith("#") and stripped == want[0])):
            continue
        if len(want) == 1:
            out.append((i, i + 1, prefix))
            if len(out) >= limit:
                return out
            continue

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
                out.append((i, j + 1, prefix))
            j += 1
        if len(out) >= limit:
            return out
    return out


def anchor_blocks(base_text: str, blocks: list[dict],
                  ignore_labels=None) -> tuple[list[dict], dict]:
    """把块锚定到**基准文本的行区间**；返回 `(带 span 的 blocks, 统计)`。

    - 区间以**行号**表达（半开 `[start_line, end_line)`），并记录被剥掉的 `prefix`
    - **贪婪 + 游标**：按阅读顺序推进，使「按序重复」的内容不被误判歧义
    - **唯一性校验**：游标之后若仍有**第二个**候选 ⇒ 记 `ambiguous` 且**不锚定**
      （宁缺勿错，绝不赌第一个 —— 实测完整文档有 15 个这种位置）
    - **豁免类**（`classify_labels` 判定的 `not_in_markdown` 或空内容）**不计入 missed**
    - **未锚定不抛**，全部记进 `stats` 由调用方判阈值
    """
    cls = classify_labels(blocks, ignore_labels)
    anchorable = set(cls["anchorable"])
    lines = base_text.split("\n")

    cursor = anchored = missed = ambiguous = 0
    residual: list[tuple[int, str, str]] = []
    out: list[dict] = []

    for entry in _sorted_blocks_with_index(blocks):
        idx = entry["idx"]                              # 原下标（edits 的键）
        blk = {k: v for k, v in entry.items() if k != "idx"}
        if idx not in anchorable:
            out.append({**blk, "start_line": None, "end_line": None, "prefix": ""})
            continue

        cands = _find_candidates(lines, blk.get("content") or "", cursor)
        if not cands:
            missed += 1
            residual.append((idx, blk.get("label") or "",
                             (blk.get("content") or "").split("\n")[0][:48]))
            out.append({**blk, "start_line": None, "end_line": None, "prefix": ""})
            continue
        if len(cands) > 1:
            # 游标之后还有第二个候选 ⇒ 无法唯一确定 ⇒ 拒绝锚定（宁缺勿错）
            ambiguous += 1
            residual.append((idx, blk.get("label") or "", "歧义：多个候选"))
            out.append({**blk, "start_line": None, "end_line": None, "prefix": ""})
            continue

        s, e, prefix = cands[0]
        cursor = e
        anchored += 1
        out.append({**blk, "start_line": s, "end_line": e, "prefix": prefix})

    return out, {
        "total": len(blocks),
        "exempt": len(cls["exempt"]),
        "anchorable": len(anchorable),
        "anchored": anchored,
        "missed": missed,
        "ambiguous": ambiguous,
        "residual_miss": residual,
        "not_in_markdown": cls["not_in_markdown"],
    }


def _sorted_blocks_with_index(blocks: list[dict]) -> list[dict]:
    """按阅读顺序排序（保留 `idx` 供调用方按下标索引；`edits` 用的就是它）。

    实测：**被忽略的 `number` 块的 `block_order` 为 null**，不能单拿 order 排序；
    `global_block_id` 实测**全序单调**（文档级全局值），是可靠主序。
    ⚠️ **键必须落在同一空间**：不能"有 gid 用 gid、没有用 page*100000+order" ——
    混用会把缺 gid 的块推到极端。故**只要有一个块缺 gid，整体退化**为 (page, order)。
    """
    tagged = [{"idx": i, **b} for i, b in enumerate(blocks)]
    if all(t.get("global_block_id") is not None for t in tagged):
        tagged.sort(key=lambda t: t["global_block_id"])
    else:
        logger.warning("部分块缺 global_block_id，整体退化为 (page, order) 排序")
        tagged.sort(key=lambda t: (t.get("page") or 0, t.get("order") or 0))
    return tagged


def splice(base_text: str, blocks: list[dict], edits: dict[int, str]) -> str:
    """按区间拼回文本：**只替换 `edits` 里的块**，其余区间原样取回原行。

    - `edits` 的键是 `blocks` 的**下标**（与 `anchor_blocks` 返回的列表同序）
    - 编辑块时**保留其 `prefix`**（复验 #4）：否则改标题会丢 `## `，
      直接改变 `parse_markdown` 的标题型判定
    - **区间重叠 ⇒ 抛 `ValueError`**：静默跳过 = 用户那次编辑被悄悄丢掉，比报错坏得多

    ⚠️ 这是本方案的地基：`edits` 为空时等价于 `base_text` 本身（**逐字节**），
    因为未编辑区间是**原样取回原行**而不是"重新生成"。故"往返保真"不需要证明。
    """
    lines = base_text.split("\n")
    ops = sorted(
        ((b["start_line"], b["end_line"], b.get("prefix") or "", edits.get(i))
         for i, b in enumerate(blocks) if b.get("start_line") is not None),
        key=lambda t: t[0],
    )

    out: list[str] = []
    cursor = 0
    for s, e, prefix, new in ops:
        if s < cursor:
            raise ValueError(f"layout 区间重叠：[{s}, {e}) 与游标 {cursor}")
        out.extend(lines[cursor:s])
        if new is None:
            out.extend(lines[s:e])                       # 未编辑 ⇒ 原行原样
        else:
            out.extend((prefix + ln).rstrip() if k == 0 else ln
                       for k, ln in enumerate(new.split("\n")))
        cursor = e
    out.extend(lines[cursor:])
    return "\n".join(out)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py -v`
Expected: PASS（黄金夹具全绿；真实夹具那条无夹具时 SKIPPED）

- [ ] **Step 5: **诊断残留未命中**（实测 41 个里扣除 30 个豁免后剩 11 个，成因未知）**

Run:
```bash
export PYTHONUTF8=1 && REQUIRE_LAYOUT_FIXTURE=1 D:/Python/python.exe -c "
import json,pathlib,sys; sys.path.insert(0,'.')
from app.ocr.layout import anchor_blocks, extract_layout
d=pathlib.Path('data/_ocr_reference')
base=(d/'reference.md').read_text(encoding='utf-8')
bl=extract_layout([json.loads(l) for l in (d/'raw.jsonl').read_text(encoding='utf-8').split(chr(10)) if l.strip()])['blocks']
_,st=anchor_blocks(base,bl)
print('可锚定',st['anchorable'],'已锚定',st['anchored'],'未命中',st['missed'],'歧义',st['ambiguous'],'豁免',st['exempt'])
for i,lab,head in st['residual_miss'][:15]: print(f'  [{i}] {lab}: {head!r}')
"
```
Expected: 打印残留未命中清单。**逐条判断成因**（内容被重排？被合并进别的块？渲染形态不同？），
并把结论**写回本步**（下面留空行供填写）：

- 残留成因 1：
- 残留成因 2：

> ⚠️ **不得为了凑 `missed == 0` 而放宽归一化**。若残留确有正当成因（如某类块本就不进
> markdown），应加入**豁免名单并给出实测理由**；否则它是真缺陷，必须报告。

- [ ] **Step 6: pyright + 提交**

```bash
pyright app/ocr/layout.py tests/test_layout_anchor.py
git add app/ocr/layout.py tests/test_layout_anchor.py
git commit -m "feat: 块↔行区间锚定（三条归一化 + 唯一性校验）与区间拼接（保留前缀）"
```

---

### Task 4: 两阶段接入导入流程 + fail-closed 验收脚本

**Files:**
- Modify: `app/ocr/layout.py`（加 `save_raw_layout_safely` / `finalize_layout`）
- Modify: `app/ocr/paddle_api.py::_download_markdown`（约 380-408 行）
- Modify: `app/routes/import_routes.py::_process_import`（约 783 行）
- Create: `scripts/verify_layout_anchor.py`
- Test: `tests/test_ocr_layout.py`（追加）

**Interfaces:**
- `save_raw_layout_safely(records, output_dir, params=None)` —— 落 `layout.raw.json`（**永不抛出**）
- `finalize_layout(output_dir, base_text, ignore_labels=None) -> dict | None` —— 锚定并落 `layout.json`

- [ ] **Step 1: 写失败测试（**注意别写成空转**）**

在 `tests/test_ocr_layout.py` **追加**：

```python
def _one_block_records(tag="x"):
    return [{"result": {"layoutParsingResults": [{"prunedResult": {
        "width": 1, "height": 1, "parsing_res_list": [
            {"block_label": "text", "block_content": tag, "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": 0}]}}]}}]


def test_save_raw_layout_safely_swallows_errors(monkeypatch, tmp_path):
    """★ 落盘失败**不得抛出**（spec §5.3）。

    ⚠️ 必须 patch **真正执行写入的那一步**：`save_raw_layout_safely` 自己 `write_text`，
    若照旧 patch `save_layout` 则**永不触发** —— 测试会**空转恒绿**（这正是复验 #8）。
    故这里 patch `Path.replace`（原子写最后一步）。
    """
    import app.ocr.layout as mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mod.Path, "replace", boom)
    mod.save_raw_layout_safely(_one_block_records(), str(tmp_path))   # 不抛即通过


def test_save_raw_layout_safely_noop_without_output_dir():
    import app.ocr.layout as mod
    mod.save_raw_layout_safely(_one_block_records(), None)


def test_save_raw_layout_safely_skips_when_blocks_empty(tmp_path):
    """★ 空 blocks **不落盘** —— 否则产生「有 layout 但 0 块」，审查页渲染空白页，
    绕过 spec §13 的降级契约（前提是"**没有** layout"）"""
    import app.ocr.layout as mod
    mod.save_raw_layout_safely(
        [{"result": {"layoutParsingResults": [{"prunedResult": {"width": 10, "height": 20}}]}}],
        str(tmp_path))
    assert not (tmp_path / mod.LAYOUT_RAW_FILENAME).exists()


def test_save_raw_layout_safely_survives_malformed_blocks(tmp_path):
    """畸形解析结果不得抛出（覆盖 AttributeError 一类）"""
    import app.ocr.layout as mod
    mod.save_raw_layout_safely([{"result": {"layoutParsingResults": [
        {"prunedResult": {"width": 1, "height": 1,
                          "parsing_res_list": ["not-a-dict", None,
                                               {"block_label": "text", "block_content": "x",
                                                "block_bbox": [0, 0, 1, 1], "block_order": 1,
                                                "global_block_id": 0}]}}]}}], str(tmp_path))


def test_finalize_layout_anchors_persists_and_records_params(tmp_path):
    """两阶段收尾：消费 raw → 锚定 → 落 layout.json（含 span / 指纹 / 生效参数）"""
    import app.ocr.layout as mod
    mod.save_raw_layout_safely(_one_block_records("AAA"), str(tmp_path),
                               params={"markdownIgnoreLabels": ["number"]})
    stats = mod.finalize_layout(str(tmp_path), "AAA\n\nBBB")
    assert stats["missed"] == 0 and stats["anchored"] == 1

    lay = mod.load_layout(tmp_path)
    assert lay["blocks"][0]["start_line"] == 0 and lay["blocks"][0]["end_line"] == 1
    assert lay["base_sha256"] == mod.base_sha256("AAA\n\nBBB"), "必须记录基准指纹"
    assert lay["ocr_params"]["markdownIgnoreLabels"] == ["number"], "必须记录生效参数"
    assert not (tmp_path / mod.LAYOUT_RAW_FILENAME).exists(), "成功后应删除 raw"


def test_finalize_layout_keeps_raw_on_failure(tmp_path):
    """★ 锚定失败时**保留 raw** 便于排查/重试（复验 #5）"""
    import app.ocr.layout as mod
    mod.save_raw_layout_safely(_one_block_records(), str(tmp_path))
    (tmp_path / mod.LAYOUT_RAW_FILENAME).write_text("{ 坏 json", encoding="utf-8")
    assert mod.finalize_layout(str(tmp_path), "任意") is None
    assert (tmp_path / mod.LAYOUT_RAW_FILENAME).exists(), "失败不得删 raw"


def test_finalize_layout_clears_stale_layout_on_rerun(tmp_path):
    """★ 重跑时**不得把旧 layout.json 与新文本混用**（复验 #5：没有代际键会错配）"""
    import app.ocr.layout as mod
    old = {"version": 1, "pages": [{"index": 1, "width": 1, "height": 1}],
           "blocks": [{"label": "text", "content": "OLD", "start_line": 0, "end_line": 1,
                       "prefix": "", "page": 1, "bbox": [0, 0, 1, 1], "order": 1,
                       "global_block_id": 0}], "base_sha256": "stale"}
    mod.save_layout(old, tmp_path)
    mod.save_raw_layout_safely(_one_block_records("NEW"), str(tmp_path))

    stats = mod.finalize_layout(str(tmp_path), "NEW")
    lay = mod.load_layout(tmp_path)
    assert stats["anchored"] == 1
    assert lay["base_sha256"] == mod.base_sha256("NEW") and lay["base_sha256"] != "stale"


def test_finalize_layout_noop_without_raw(tmp_path):
    import app.ocr.layout as mod
    assert mod.finalize_layout(str(tmp_path), "任意文本") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v -k "raw or finalize"`
Expected: FAIL —— `AttributeError: module 'app.ocr.layout' has no attribute 'save_raw_layout_safely'`

- [ ] **Step 3: 实现两阶段**

在 `app/ocr/layout.py` **追加**：

```python
def _write_json_atomic(payload: dict, path: Path) -> None:
    """原子写：先写 .tmp 再 replace。抽出来便于单测注入失败。"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def save_raw_layout_safely(records: list[dict], output_dir: str | None,
                           params: dict | None = None) -> None:
    """**第一阶段**：把**未锚定**的块落 `layout.raw.json`；失败只记 WARN、**绝不抛出**。

    为什么分两阶段：锚定必须以**审查页实际使用的那份文本**为基准，而那是
    `_process_import` 里清洗/处理后的产物，OCR 客户端看不到。

    ⚠️ **blocks 为空时不落盘**（否则审查页渲染空白页，绕过 spec §13 的降级契约）。
    ⚠️ 异常**必须收窄**（全局规则 §1.2）；`params` 一并存下，供锚定阶段用同一份
    `markdownIgnoreLabels`（复验 #6：不得把动态参数当固定事实）。
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

    写下的 `layout.json` 含：`blocks`（带 span + prefix）、`base_sha256`（指纹，复验 #5）、
    `ocr_params`（生效参数，复验 #6）、`stats`。

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
        layout = {
            "version": LAYOUT_VERSION,
            "pages": raw.get("pages") or [],
            "blocks": anchored,
            "base_sha256": base_sha256(base_text),
            "ocr_params": params,
            "stats": {k: v for k, v in stats.items() if k != "residual_miss"},
        }
        # ⚠️ 重跑时先清掉旧的 layout.json，避免"旧 span + 新文本"错配（复验 #5）
        (out / LAYOUT_FILENAME).unlink(missing_ok=True)
        save_layout(layout, out)
        if stats["missed"] or stats["ambiguous"]:
            logger.warning("layout 锚定：未命中 %d / 歧义 %d / 可锚定 %d（详见 layout.json.stats）",
                           stats["missed"], stats["ambiguous"], stats["anchorable"])
        raw_path.unlink(missing_ok=True)      # ✅ 只有**成功**才删 raw
        return stats
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError,
            TypeError, ValueError, AttributeError) as e:
        logger.warning("layout 锚定失败（不影响导入；**保留 %s 便于排查**）: %s",
                       LAYOUT_RAW_FILENAME, e)
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: PASS

- [ ] **Step 5: 接入 `_download_markdown`（第一阶段）**

读 `app/ocr/paddle_api.py` 约 380-408 行，把循环改为**先物化 records，再旁路落 raw**：

```python
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

        # 旁路落**未锚定**的版面块 + **本次生效参数**（审查页用）。
        # 失败只记 WARN，不中断导入（spec §5.3）。
        save_raw_layout_safely(records, output_dir, self._optional_payload())

        return "\n".join(parts)
```

顶部 import 加：`from app.ocr.layout import save_raw_layout_safely`

- [ ] **Step 6: 接入 `_process_import`（第二阶段）**

在 PDF 分支、**`md_text = clean_ocr_text(md_text)` 之后、`_update_task(..., md_text=md_text, ...)` 之前**插入：

```python
        # 锚定版面块到**审查页实际使用的这份文本**，落 layout.json。
        # 失败只记 WARN，不影响导入（spec §5.3）。
        finalize_layout(Path(OUTPUT_DIR) / task_id, md_text)
```

import 区加 `from app.ocr.layout import finalize_layout`。

> ℹ️ **基准取未清洗文本**：实测 `clean_ocr_text` 对 `markdown.text` **删 0 行**
> （`markdownIgnoreLabels` 已排除页眉/页脚/页码）。故**清洗前后等价**；此处放在
> `clean_ocr_text` 之后是**就近取用**，不是语义依赖。若 Task 1 Step 4 复核发现
> 清洗改动 >0 行，**必须改为在 `clean_ocr_text` 之前锚定**（基准=块同源的那份）。

- [ ] **Step 7: 跑相关增量测试**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py tests/test_layout_anchor.py tests/test_paddle_vl_v2.py tests/test_import.py -v`
Expected: 全 PASS（`test_paddle_vl_v2.py` 覆盖 `_download_markdown` 既有行为，**必须不被破坏**）

- [ ] **Step 8: 写 fail-closed 验收脚本**

创建 `scripts/verify_layout_anchor.py`：

```python
"""P0 门禁验收：对真实产物检查**锚定覆盖率 + 残留解释 + 逐字节相等**。

用法：D:/Python/python.exe scripts/verify_layout_anchor.py

退出码：0 = 通过；1 = 有不达标产物；**2 = 一份可检查产物都没有（fail-closed）**
"""
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.ocr.layout import base_sha256, load_layout, splice  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUTPUTS = REPO / "data" / "outputs"
MAX_MISS_RATE = 0.02          # 残留未命中率上限（实测 11/563 ≈ 2%）；超了必须逐条解释


def main() -> int:
    checked = failed = 0
    for d in sorted(OUTPUTS.glob("*/")):
        layout = load_layout(d)
        if layout is None:
            continue
        mds = list(d.glob("*.md"))
        if len(mds) != 1:
            print(f"跳过 {d.name}：期望恰好 1 个 .md，实有 {len(mds)}")
            continue

        checked += 1
        base = mds[0].read_text(encoding="utf-8")
        blocks = layout.get("blocks") or []
        st = layout.get("stats") or {}
        anchorable = st.get("anchorable", 0)
        missed, ambiguous = st.get("missed", 0), st.get("ambiguous", 0)

        identical = splice(base, blocks, {}) == base
        rate = (missed / anchorable) if anchorable else 1.0
        ok = identical and ambiguous == 0 and anchorable > 0 and rate <= MAX_MISS_RATE

        # ⚠️ 基准指纹必须对得上（复验 #5：防止"旧 span + 新文本"错配）
        fp_ok = layout.get("base_sha256") == base_sha256(base)

        print(("✅ " if ok and fp_ok else "❌ ") + f"{d.name}  可锚定 {anchorable} "
              f"未命中 {missed}（{rate:.1%}）歧义 {ambiguous} 逐字节相等={identical} 指纹={fp_ok}")
        if not (ok and fp_ok):
            failed += 1

    print(f"\n共检查 {checked} 份，失败 {failed} 份")
    if checked == 0:
        print("❌ 没有可校验的成对产物 —— 先跑一次真实导入产生 layout.json。"
              "（门禁 fail-closed：未验证不算通过）")
        return 2
    return 1 if failed else 0


sys.exit(main())
```

- [ ] **Step 9: 跑验收**

Run: `export PYTHONUTF8=1 && D:/Python/python.exe scripts/verify_layout_anchor.py; echo "退出码=$?"`
Expected: 首次打印 `共检查 0 份` 与提示，**退出码 2**（预期）。P0 最终验收 = 跑一次真实导入后重跑，应全 ✅ 且退出码 0。

- [ ] **Step 10: 量一次锚定耗时（门槛：1000 块 ≤ 2000 ms）**

实测基准（2026-10-04）：**650 块 / 1244 行 → 68 ms**。若你的产物明显更慢：

Run:
```bash
export PYTHONUTF8=1 && D:/Python/python.exe -c "
import json,pathlib,sys,time; sys.path.insert(0,'.')
from app.ocr.layout import anchor_blocks, load_layout
for d in sorted(pathlib.Path('data/outputs').glob('*/')):
    lay=load_layout(d)
    if not lay: continue
    md=list(d.glob('*.md'))[0].read_text(encoding='utf-8')
    t=time.perf_counter(); _,st=anchor_blocks(md,lay['blocks']); dt=(time.perf_counter()-t)*1000
    print(f'{d.name}: 块 {st[\"anchorable\"]} 耗时 {dt:.0f} ms')
"
```
Expected: `耗时` 远低于 2000 ms。**超了** ⇒ 改用行哈希索引（首行 → 行号表）再局部校验，
并在提交信息里写明。

- [ ] **Step 11: pyright + 提交**

```bash
pyright app/ocr/layout.py app/ocr/paddle_api.py app/routes/import_routes.py scripts/verify_layout_anchor.py tests/test_ocr_layout.py
git add app/ocr/layout.py app/ocr/paddle_api.py app/routes/import_routes.py scripts/verify_layout_anchor.py tests/test_ocr_layout.py
git commit -m "feat: 两阶段落盘 layout（带指纹与生效参数）+ fail-closed 验收脚本"
```

---

### Task 5: `.md` 上传走源码审查页（spec §20）

> 复验 #10：spec §20 定了这个入口，但**没有任何 Task 实现它** —— P1/P2 会假设它存在。

**Files:**
- Modify: `app/routes/import_routes.py::_process_import`（`.md` 分支，约 749-755 行）
- Modify: `app/routes/import_routes.py::review_page`（约 1191 行）
- Test: `tests/test_import.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_import.py` **追加**：

```python
def test_md_upload_pauses_for_review(auth_client, monkeypatch, tmp_path):
    """★ `.md` 上传必须**停在待审查**（此前是直进 Phase 2，完全没有审查环节）。

    ⚠️ 隔离：三个路径常量都绑定在**消费方模块**的命名空间里，必须按消费方 patch。
    """
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    for n in ("lance", "up", "out"):
        (tmp_path / n).mkdir(parents=True, exist_ok=True)
    from app.database import init_db, get_db
    init_db()

    import io
    md = "# 测试\n\n## 1 总则\n\n内容。\n"
    resp = auth_client.post("/import/upload",
                            files={"file": ("t.md", io.BytesIO(md.encode()), "text/markdown")},
                            data={"code": "T 1-2024", "title": "测试"})
    assert resp.status_code == 200
    task_id = resp.headers.get("X-Import-Task-Id")
    assert task_id, "上传响应必须带任务号（否则前端无法跨页追踪）"

    with get_db() as conn:
        row = conn.execute("SELECT status FROM import_tasks WHERE task_id=?", (task_id,)).fetchone()
    assert row and row["status"] == "review_needed", f"`.md` 应停在待审查，实为 {row and row['status']}"
```

> ⚠️ 该用例会改变既有行为，**先跑 `tests/test_import.py` 看有没有别的用例依赖"直进 Phase 2"**，
> 有则一并更新并在提交信息里说明。

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_import.py -k md_upload_pauses -v`
Expected: FAIL（status 不是 `review_needed`）

- [ ] **Step 3: 改 `.md` 分支**

把 `_process_import` 的 `.md` 分支从「清洗后直接 `_process_import_phase2`」改为**清洗后写 `review_needed`**：

```python
        if ext == ".md":
            md_text = path.read_text(encoding="utf-8")
            md_text = clean_ocr_text(md_text)      # 先清洗再审查（否则用户改完又被清洗一遍）
            _update_task(task_id, md_text=md_text, title=title, code=code,
                         file_path=file_path, file_name=path.name, file_hash=file_hash,
                         spec_status=status, replaced_by_code=replaced_by_code,
                         status="review_needed", progress=50, message="等待人工审查...")
            log_action("import", "INFO", "MD 文件待审查",
                       detail=json_detail({"task_id": task_id, "code": code}),
                       username="")
            return
```

> ⚠️ **不要**在这里调 `_process_import_phase2`；提交后由 `confirm_review` 启动。
> `log_action` 的 `username` 按该函数既有取法填（若函数签名里没有 request，就沿用本函数现有写法）。

- [ ] **Step 4: 按扩展名分派审查模板**

`review_page` 目前硬编码 `partials/ocr_review.html`。改为：

```python
    task = _get_task(task_id)
    if not task or task.get("status") != "review_needed":
        ...
    # 单一判据：文件名扩展名（spec §20.2）。不另加状态字段。
    is_md = str(task.get("file_name") or "").lower().endswith(".md")
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/ocr_review.html" if is_md else "partials/ocr_review_blocks.html",
        "task_id": task_id,
        "hide_tree": True,
    })
```

> ℹ️ `ocr_review_blocks.html` 在 **P1** 才创建。**本 Task 只做分派与 `.md` 入口**；
> 在 P1 交付前，非 `.md` 分支会 500 —— 因此**本 Task 与 P1 必须同批**，或临时把非 md
> 分支仍指向 `ocr_review.html` 并在 P1 改。**二选一，在提交信息里写明选了哪个。**

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_import.py -v`
Expected: 全 PASS

- [ ] **Step 6: pyright + 提交**

```bash
pyright app/routes/import_routes.py tests/test_import.py
git add app/routes/import_routes.py tests/test_import.py
git commit -m "feat: .md 上传走源码审查页（入口分派按扩展名）"
```

---

## P0 出口判据（全部满足才算通过，spec §18）

- [ ] `test_splice_with_no_edits_is_byte_identical` **PASS** —— 不编辑即逐字节相等
- [ ] `test_splice_preserves_hash_prefix_when_editing_a_title` **PASS** —— 改标题不丢 `## `
- [ ] `test_splice_raises_on_overlapping_spans` **PASS** —— 重叠显式失败，不静默丢编辑
- [ ] `test_anchor_records_ambiguity_and_refuses_to_guess` **PASS** —— 歧义拒绝锚定
- [ ] **真实夹具**上 `REQUIRE_LAYOUT_FIXTURE=1 … pytest tests/test_layout_anchor.py` 全 **PASS（非 SKIPPED）**
- [ ] Task 3 Step 5 的**残留未命中逐条有成因**（写回该步），且**未放宽归一化凑数**
- [ ] 提取结构**不含**签名凭据（合成 + 真实两条断言 PASS）；夹具落盘**已脱敏**
- [ ] `layout.json` 记录 `base_sha256` 与 `ocr_params`
- [ ] 锚定耗时 ≤ 2000 ms（1000 块量级）
- [ ] `paddle_api` 既有测试未被破坏（`test_paddle_vl_v2.py` PASS）
- [ ] 跑一次**真实导入**后 `scripts/verify_layout_anchor.py` **全 ✅ 且退出码 0**
- [ ] 增量测试 + `pyright` 无新增 error

> **任一条不满足 ⇒ 不得进入 P1**（spec §18：锚定一旦有偏差，P2 之后所有编辑都会以「**静默改写 `md_text`**」的形式污染导入结果）。

---

## Decision ledger（/plan-eng-review 2026-10-04）

### R1: 首轮评审发现整改
Finding: A1 页码索引 · A2 覆盖边界 · A3 空 blocks · Q1 排序键 · Q2 异常范围 · T1–T5 · F1 pyright 调用
Plan baseline: 原计划的实现与验证步骤
Runtime evidence: 实测 `pyright --version` → 1.1.410；`python -m pyright` → `No module named pyright`；`_download_markdown` 仅存在于 `PaddleVLClient`
Question D1: 哪些采纳进 P0 计划
State: approved
Actual answer: 全选
Accepted scope: 已改入正文
History: —

### R2: `.md` 上传的审查入口
Finding: 用户提出 —— 保留现有「左 md 源码 / 右预览」作为 `.md` 审查页
Plan baseline: spec §2.2 未涵盖
Runtime evidence: `_process_import` 的 `.md` 分支直进 Phase 2（`import_routes.py:749-755`）
Question D2: Include / Defer / Hold
State: approved
Actual answer: Include
Accepted scope: spec §20 + **本计划 Task 5**（此前只落 spec、无 Task）
History: 复验 #10 指出"只落 spec 无 Task"，已补 Task 5

### R3: P0 形态
Finding: 重建整篇 `markdown.text` 是反推未公开渲染器
Plan baseline: 拼装规则 + 往返对拍
Question D3: span / 保持现方案 / 先量命中率
State: approved
Actual answer: 改为 span 方案
Accepted scope: 正文重写为 span 方案
History: —

### R4: 复验后的第二轮整改（**由真机实测驱动**）
Finding: 外部复核 10 条 + 实测新出 3 条
Runtime evidence: 2026-10-04 两次真实 API 调用（6 页 + 完整 57 页）
Question D4: 完整转换 + 怎么走
State: approved
Actual answer: 跑完整 57 页；重写计划
Accepted scope: **v3 重写**，含以下裁定：

| 复验发现 | 裁定 | 依据 |
| --- | --- | --- |
| #1 清洗不同源 | ❌ **不成立** | 真机实测：`clean_ocr_text` 删 **0 行**（两次一致） |
| #2 三份不同 base | ⚠️ 部分成立 | 已统一为「未清洗 `markdown.text`」，并加 `base_sha256` 指纹 |
| #3 贪婪无唯一性 | ✅ **成立** | 子集 0 歧义、**完整文档 15 个** ⇒ 加唯一性校验，拒绝锚定 |
| #4 splice 丢 `## ` | ✅ 成立 | span 记 `prefix`，splice 原样保留 + 专项用例 |
| #5 缺代际/指纹 | ✅ 成立 | 加 `base_sha256`、`ocr_params`、重跑清旧 `layout.json`、失败保留 raw |
| #6 参数当固定事实 | ✅ **成立且加重** | 实测标签 11 种（含 `formula_number`/`vision_footnote`），词表随参数变 ⇒ 记录生效参数 |
| #7 schema 太松 | ✅ 成立 | 严格校验：版本相等、逐项类型、span 区间 |
| #8 修复本身空转 | ✅ 成立 | 改为 patch 真正的写入步（`Path.replace`） |
| #9 规模门禁是提示 | ✅ 成立 | 实测 68 ms/650 块 ⇒ 阈值写死 2000 ms + 具体步骤 |
| #10 `.md` 入口无 Task | ✅ 成立 | 新增 **Task 5** |
| **新 A：归一化缺「行间空行」** | ✅ **两轮复核都没发现** | 实测：旧规则 541/593，补规则后 **552/593** |
| **新 B：门禁不能一刀切** | ✅ | 41 未命中里 30 个是豁免类（`formula_number` 21 + `image` 9，实测不进 markdown） |
| **新 C：残留 11 个成因未知** | ⏳ 待诊断 | 写成 Task 3 Step 5，**不假装已知** |

**Approval readiness: PASS** — R1(D1) / R2(D2) / R3(D3) / R4(D4)

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | `/plan-eng-review` → Codex ×2 | Independent 2nd opinion | 2 | completed | 16 findings |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN | 11 issues, 1 critical gap |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** provider=codex, phase=plan-review, **completed ×2**。第一轮 6 条全部核验成立；第二轮 10 条中 **7 条成立**、3 条被真机实测推翻（#1 清洗、#3 的 gid 部分、#9 性能）。两轮共 16 条，**均未发现「行间空行」那条** —— 它由真机实测揭示。
- **VERDICT:** ENG REVIEW **仍有未决项**（Task 3 Step 5 的残留成因待填；P1 前须跑真实导入复验）— **eng review required**。
  - 问题分解：Architecture 3 · Code Quality 2 · Performance 1 · Test gaps 5；另有 1 处事实纠错。
  - **critical gap = A3**（空 blocks ⇒ 静默空白页），已用「空则不落盘」+ 回归用例覆盖。
  - 待办（**不是未决决策**）：Task 3 Step 5 的残留未命中成因需在实现时当场诊断并填写。

NO UNRESOLVED DECISIONS
