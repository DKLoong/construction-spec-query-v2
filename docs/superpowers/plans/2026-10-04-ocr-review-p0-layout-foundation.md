# OCR 审查页改版 · P0 地基 实施计划（**span 方案**）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OCR 的版面块与**它自己产出的那篇 markdown** 建立**可验证的区间对应**，使「只改被编辑的块、其余逐字不变」成为**结构上恒真**的性质。

**Architecture:** 真源是 `markdown.text`（API 已产出的规范 Markdown），**不重建**。新增 `app/ocr/layout.py` 做三件事：(a) 从 `prunedResult.parsing_res_list` **白名单式**提取块（天然剔除签名 URL）；(b) 把每个块**锚定**到基准文本的**行区间**；(c) `splice` 按区间拼回 —— **未编辑的区间原样取回原行**，故不编辑即逐字节相等。锚定发生在 `_process_import` 内、`clean_ocr_text` **之后**、`_update_task` **之前**，与审查页实际使用的文本**同源**。

**Tech Stack:** Python 3.14 / FastAPI / pytest / PyMuPDF(`fitz`) / PaddleOCR-VL V2 API

**Spec:** `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`

> **本版为 2026-10-04 `/plan-eng-review` 后的重写**。上一版「反推拼装规则（前缀表 + 忽略表）+ 往返对拍」被 Outside Voice 判定为**过度耦合**（在反推一个未公开渲染器），已废弃。决策依据见文末 Decision ledger 的 **R3**。

---

## Global Constraints

以下约束对本计划**每个 Task** 都生效：

1. **`md_text` 对外契约不变** —— `parse_markdown` / `plain_text` / `build_embed_text` **零改动**（spec §17）。
2. **不新增依赖** —— 只用已在用的 PyMuPDF。
3. **测试与脚本绝不写入真实 `data/spec_query.db` / `data/uploads` / `data/outputs` / `lance_db`**；需要写盘一律用 `tmp_path`、或 `data/_ocr_reference/`（不入库）。
4. **落盘的任何 OCR 派生物不得含签名凭据** —— 不得出现 `authorization=`，不得出现 `inputImage` / `preprocessedImages` / `outputImages`。**落盘前必须脱敏**（不是"靠 .gitignore"）。
5. **门禁必须 fail-closed** —— 任何"夹具缺失 / 检查 0 份"的情形一律**非零退出**，不得静默通过。
6. 每个 Task 结束跑**增量测试** + `pyright`，**不得新增 error**，再提交。
7. **pyright 用 npm 全局二进制**：`pyright <显式路径>`。
   ⚠️ **不得写成 `python -m pyright`** —— 实测（2026-10-04）：`pyright --version` → 1.1.410；`D:/Python/python.exe -m pyright` → `No module named pyright`。`pytest` 仍可走 `python -m pytest`。
8. **提交时逐个 `git add <文件>`，不得用 `git add .`**（工作区可能有他人改动）。

## 产物覆盖边界（写清，免得读者以为 layout 普遍存在）

`layout.json` **只在 PaddleOCR-VL 后端 + 扫描件路径产生**：

| 路径 | 有无 layout | 原因 |
| --- | --- | --- |
| `.pdf` + PaddleOCR-VL（扫描件或 `force_ocr`） | ✅ 有 | 走 `PaddleVLClient._download_markdown` |
| `.pdf` + `accurate-basic` / `custom` 后端 | ❌ 无 | 这两个客户端**自己写 md**，没有 `_download_markdown`（`paddle_api.py:99` / `:574`） |
| `.pdf` **非扫描件** | ❌ 无 | `is_scanned()` 为假 ⇒ 走 `extract_text()`，**连 OCR 都不跑** |
| `.md` 上传 | ❌ 无 | 直读文件（该入口另走源码审查页，见 spec §20） |

没有 layout 时审查页**降级为纯预览**（spec §13），不报错。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/ocr/layout.py`（新建） | 块的**提取 / 脱敏 / 锚定 / 拼接 / 落盘读取**。纯函数 + 文件 IO，无 OCR 客户端依赖、无 DB 依赖 |
| `scripts/capture_ocr_reference.py`（新建） | 一次性抓参考夹具（**会调外部付费 API**，仅手动运行；**落盘前脱敏**） |
| `scripts/verify_layout_anchor.py`（新建） | P0 验收脚本：对真实产物跑**锚定覆盖率**，**检查 0 份时非零退出** |
| `tests/fixtures/ocr_layout_golden.jsonl`（新建，**入库**） | **合成**黄金夹具：手写的最小 JSONL（**无真实 OCR 内容、无凭据**），让骨架测试在 CI 里真跑 |
| `tests/test_ocr_layout.py`（新建） | 提取 / 脱敏 / 落盘读取 / schema |
| `tests/test_layout_anchor.py`（新建） | **锚定 + 拼接**（核心门禁） |
| `app/ocr/paddle_api.py`（改 `_download_markdown`） | 旁路落**未经锚定**的 `layout.raw.json`（失败不中断导入） |
| `app/routes/import_routes.py`（改 `_process_import`） | `clean_ocr_text` **之后**消费 raw，锚定并落 `layout.json` |

**为什么放 `app/ocr/` 而不是 `app/parser/`**：模块的输入是 **OCR 响应结构**（`parsing_res_list`），不是通用 markdown；且 `md_parser.py` 是全仓最承重组件（115 调用方 / 120 用例 / 正文守恒测试），新文件放旁边会误导读者以为同族可改（spec §2.2 明确 parser 零改动）。

---

### Task 1: 参考夹具（**会调用一次外部 OCR API**）与两项实测

> ⚠️ **本 Task 是唯一会花钱的一步。** 运行前须确认可以调用。

**Files:**
- Create: `scripts/capture_ocr_reference.py`
- Create（运行产物，**不入库**）: `data/_ocr_reference/raw.jsonl`（**已脱敏**）、`reference.md`

**Interfaces:**
- Consumes: `app.ocr.paddle_api.create_ocr_client()`、`PaddleVLClient._submit_task` / `_poll_result`
- Produces: `data/_ocr_reference/{raw.jsonl, reference.md}`；并在 stdout 打印两项实测结果（见 Step 4/5）

- [ ] **Step 1: 写抓取脚本（含落盘前脱敏）**

```python
"""一次性（可重跑）抓取 P0 参考夹具：OCR 原始 JSONL（**脱敏后**）+ 同批页面的 markdown。

⚠️ 会调用**外部付费 OCR API**。仅在需要建立/刷新夹具时手动运行：

    D:/Python/python.exe scripts/capture_ocr_reference.py <pdf路径> --pages 1,2,5,6,8,10

⚠️ **落盘前必须脱敏**：官网响应里 `inputImage` / `preprocessedImages` /
`outputImages.layout_det_res` 都是**带凭据的签名 URL**（`?authorization=bce-auth-v1...`）。
`.gitignore` 只防提交，不防磁盘、备份与分发打包 ⇒ 在**内存中**完成断言与替换，再写盘。

产出（`data/` 已 gitignore ⇒ 不入库，与既有「JGJ107 语料缺则跳过」同一约定）：
    data/_ocr_reference/raw.jsonl      已脱敏的原始 JSONL
    data/_ocr_reference/reference.md   同批页面的 markdown（锚定基准）
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

# 签名 URL 的形状：http(s)://...?authorization=...；整串替换掉，不留残余
_SIGNED_URL = re.compile(r'https?://[^"\s\\]*authorization=[^"\s\\]*')


def _scrub(raw: str) -> tuple[str, int]:
    """把所有签名 URL 替换为占位符，返回 (脱敏文本, 替换次数)。

    正则的字符类排除了引号，故匹配在 JSON 字符串的收尾引号**之前**停下；
    替换成裸占位符后，JSON 里仍是合法的 `"<redacted-signed-url>"`。
    """
    return _SIGNED_URL.sub("<redacted-signed-url>", raw)


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

    # ① 先在**内存中**证明确实拿到了真响应（有签名 URL），再脱敏落盘
    n_signed = len(_SIGNED_URL.findall(raw))
    assert n_signed > 0, "响应里没有签名 URL —— 夹具不是真响应，停下来报告"
    scrubbed, n_replaced = _scrub(raw)
    assert "authorization=" not in scrubbed, "脱敏不彻底"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "raw.jsonl").write_text(scrubbed, encoding="utf-8")

    # ② 基准 markdown：用**现有生产规则**（逐行 layoutParsingResults[].markdown.text 拼接）
    parts = []
    for line in scrubbed.strip().split("\n"):
        if not line.strip():
            continue
        rec = json.loads(line)
        for lp in (rec.get("result") or {}).get("layoutParsingResults") or []:
            text = (lp.get("markdown") or {}).get("text")
            if text:
                parts.append(text)
    reference = "\n".join(parts)
    (OUT_DIR / "reference.md").write_text(reference, encoding="utf-8")

    print(f"✅ 夹具已写（已脱敏：{n_signed} 处签名 URL，替换 {n_replaced} 处）")
    print(f"   reference.md = {len(reference)} 字符")
    sliced.unlink(missing_ok=True)


asyncio.run(main())
```

- [ ] **Step 2: 确认夹具目录不会被提交**

Run: `git check-ignore -v data/_ocr_reference/raw.jsonl`
Expected: 输出含 `.gitignore:11:data/`。**若没有输出，停下来报告**。

- [ ] **Step 3: 运行抓取（一次外部调用）**

Run:
```bash
export PYTHONUTF8=1 && D:/Python/python.exe scripts/capture_ocr_reference.py "data/uploads/8376e02c.pdf" --pages 1,2,5,6,8,10
```
Expected: 打印 job_id、等待、`✅ 夹具已写（已脱敏：N 处签名 URL…）`。

- [ ] **Step 4: 实测①——覆盖到哪些 label**

Run: `grep -o '"block_label": "[a-z_]*"' data/_ocr_reference/raw.jsonl | sort | uniq -c`
Expected: 打印标签分布。**记录下来**，Task 3 要用。
若缺 `table` / `figure` / `formula` 等，**换页号重跑**（每次仍是一次外部调用）。

- [ ] **Step 5: 实测②——`clean_ocr_text` 对基准文本的影响（决定锚定基准是否安全）**

Run:
```bash
export PYTHONUTF8=1 && D:/Python/python.exe -c "
from app.ai.text_clean import *  # noqa
import pathlib,sys
sys.path.insert(0,'.')
from app.parser.md_parser import _clean_title
from app.routes.import_routes import clean_ocr_text
t = pathlib.Path('data/_ocr_reference/reference.md').read_text(encoding='utf-8')
c = clean_ocr_text(t)
print('原文', len(t), '清洗后', len(c), '差', len(t)-len(c))
"
```
Expected: 打印字符数差。**这是关键判据**：
- 差为 **0** ⇒ 清洗对 markdown.text 无影响（`number`/`header`/`footer` 本就在 `markdownIgnoreLabels` 里）⇒ 锚定基准取清洗前后**等价**
- 差 **>0** ⇒ **必须**取清洗后的文本作基准（本计划已如此设计：锚定发生在 `clean_ocr_text` 之后）

> ⚠️ 若该命令因 `clean_ocr_text` 的导入路径报错，用 `grep -rn "def clean_ocr_text" app/` 找到真实模块再改 import。**不要跳过这一步** —— 它决定 P1 的 `block_content` 与审查页文本是否同源。

- [ ] **Step 6: 提交脚本**

```bash
git add scripts/capture_ocr_reference.py
git commit -m "feat: 抓取 P0 参考夹具的脚本（手动运行；落盘前脱敏）"
```

---

### Task 2: 块的提取 / 脱敏 / 落盘 / 读取

**Files:**
- Create: `app/ocr/layout.py`
- Test: `tests/test_ocr_layout.py`
- Create（**入库**）: `tests/fixtures/ocr_layout_golden.jsonl`

**Interfaces:**
- Produces:
  - `extract_layout(records: list[dict]) -> dict` → `{"version": int, "pages": [...], "blocks": [{"label","content","page","bbox","order","global_block_id"}]}`（**无 span**，span 由 Task 3 锚定）
  - `save_layout(layout: dict, output_dir: str | Path) -> str`（原子写）
  - `load_layout(output_dir: str | Path) -> dict | None`（缺失/**损坏**/**schema 不符**一律 `None`）
  - （两阶段的落盘函数 `save_raw_layout_safely` / `finalize_layout` 在 **Task 4** 才引入，见那里的 Interfaces）
  - 常量：`LAYOUT_VERSION=1`、`LAYOUT_RAW_FILENAME="layout.raw.json"`、`LAYOUT_FILENAME="layout.json"`、`IGNORE_LABELS`、`SIGNED_URL_KEYS`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_ocr_layout.py`：

```python
"""layout 提取 / 脱敏 / 落盘读取 / schema（spec §5）。

夹具两套：
  ① tests/fixtures/ocr_layout_golden.jsonl —— **合成**黄金夹具，**入库**，CI 里真跑；
  ② data/_ocr_reference/raw.jsonl —— 真实夹具，**不入库**，缺则跳过。
"""
import json
from pathlib import Path

import pytest

from app.ocr.layout import (LAYOUT_FILENAME, SIGNED_URL_KEYS, extract_layout,
                            load_layout, save_layout)

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "ocr_layout_golden.jsonl"
REF_RAW = REPO / "data" / "_ocr_reference" / "raw.jsonl"

needs_ref = pytest.mark.skipif(
    not REF_RAW.is_file(),
    reason="缺少真实夹具 data/_ocr_reference/raw.jsonl（见 scripts/capture_ocr_reference.py）",
)


def _records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in text.split("\n") if ln.strip()]


# ── 提取 ──────────────────────────────────────────────

def test_extract_layout_from_golden_shape():
    """合成黄金夹具（入库）—— 字段齐全、页码从 1 起"""
    layout = extract_layout(_records(GOLDEN))
    assert layout["version"] == 1
    assert layout["pages"] and layout["blocks"]
    for p in layout["pages"]:
        assert p["index"] >= 1 and p["width"] and p["height"]
    for b in layout["blocks"]:
        assert b["label"] and isinstance(b["content"], str)
        assert b["page"] >= 1
        assert isinstance(b["bbox"], list) and len(b["bbox"]) == 4


def test_extract_layout_page_index_is_flat_across_records():
    """★ 页码 = 跨 record 的**扁平累计序号**，不是 record 下标。

    构造「一条 record 含 2 个 layoutParsingResults（2 页）」—— 这是 record 下标写法
    **唯一会错**的形态，也是 1 页探针**测不出来**的形态。用合成用例把不变量钉住。
    """
    def _page(tag, gid):
        return {"prunedResult": {"width": 10, "height": 20, "parsing_res_list": [
            {"block_label": "content", "block_content": tag, "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": gid}]}}

    rec = {"result": {"layoutParsingResults": [_page("p1", 0), _page("p2", 1)]}}
    layout = extract_layout([rec, rec])

    assert [p["index"] for p in layout["pages"]] == [1, 2, 3, 4], \
        "页码应为跨 record 的扁平累计序号；用 record 下标会让第 2..N 页撞号"
    assert [b["page"] for b in layout["blocks"]] == [1, 2, 3, 4]


def test_extract_layout_is_whitelist_so_signed_urls_cannot_survive():
    """★ 脱敏硬约束（spec §5.2）：输出结构是**白名单构造**的，签名 URL 不可能进来。

    用合成输入（**含**签名 URL 字段）证明剔除真的发生 —— 不是两边都没有。
    """
    rec = {"result": {"layoutParsingResults": [{
        "prunedResult": {"width": 10, "height": 20, "parsing_res_list": [
            {"block_label": "content", "block_content": "x", "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": 0}]},
        "inputImage": "https://x/y?authorization=bce-auth-v1/SECRET",
        "preprocessedImages": ["https://x/y?authorization=bce-auth-v1/SECRET"],
        "outputImages": {"layout_det_res": "https://x/y?authorization=bce-auth-v1/SECRET"},
    }]}}
    dumped = json.dumps(extract_layout([rec]), ensure_ascii=False)
    assert "authorization=" not in dumped and "SECRET" not in dumped
    for key in SIGNED_URL_KEYS:
        assert key not in dumped


@needs_ref
def test_extract_layout_from_real_fixture_drops_signed_urls():
    """真实夹具同样：原始（脱敏前）含签名 URL，提取结果里没有"""
    raw = REF_RAW.read_text(encoding="utf-8")
    assert "authorization=" not in raw, \
        "真实夹具应当是**已脱敏**的（脱敏断言改在 capture 脚本里做，见 Task 1 Step 1）"
    dumped = json.dumps(extract_layout(_records(REF_RAW)), ensure_ascii=False)
    for key in SIGNED_URL_KEYS:
        assert key not in dumped


# ── 落盘 / 读取 ────────────────────────────────────────

def test_save_then_load_roundtrip(tmp_path):
    layout = {"version": 1, "pages": [{"index": 1, "width": 10, "height": 20}],
              "blocks": [{"label": "content", "content": "x", "page": 1,
                          "bbox": [0, 0, 1, 1], "order": 1, "global_block_id": 0}]}
    path = save_layout(layout, tmp_path)
    assert Path(path).name == LAYOUT_FILENAME
    assert load_layout(tmp_path) == layout


def test_load_layout_missing_returns_none(tmp_path):
    assert load_layout(tmp_path) is None


def test_load_layout_corrupt_returns_none(tmp_path):
    """损坏 json 返回 None 且不抛 —— 审查页不得因夹具损坏而 500"""
    (tmp_path / LAYOUT_FILENAME).write_text("{ not json", encoding="utf-8")
    assert load_layout(tmp_path) is None


def test_load_layout_non_dict_returns_none(tmp_path):
    """顶层不是 dict（合法 JSON 但结构不符）同样返回 None"""
    for bad in ("[1, 2]", '"str"', "null"):
        (tmp_path / LAYOUT_FILENAME).write_text(bad, encoding="utf-8")
        assert load_layout(tmp_path) is None, f"{bad} 应返回 None"


def test_load_layout_bad_schema_returns_none(tmp_path):
    """★ schema 校验：合法 JSON、dict，但缺 blocks/pages/version ⇒ 仍返回 None。

    否则 P1 拿到一个"看着有、其实没有 blocks"的 dict，会在渲染时炸。
    """
    for bad in ('{"foo": 1}', '{"version": 1, "blocks": []}', '{"version": 1, "pages": "x"}'):
        (tmp_path / LAYOUT_FILENAME).write_text(bad, encoding="utf-8")
        assert load_layout(tmp_path) is None, f"{bad} 应判为 schema 不符"


def test_load_layout_bad_utf8_returns_none(tmp_path):
    """非法 UTF-8 按损坏处理，不抛"""
    (tmp_path / LAYOUT_FILENAME).write_bytes(b"\xff\xfe\x00not utf8")
    assert load_layout(tmp_path) is None


def test_save_layout_leaves_no_tmp_file(tmp_path):
    save_layout({"version": 1, "pages": [], "blocks": []}, tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
```

> ⚠️ 最后一条 `test_save_layout_leaves_no_tmp_file` 用空结构是**故意的**（只测原子写），
> 与 Task 4 的 `save_raw_layout_safely` 的"空则不落盘"是两回事 —— **`save_layout` 本身不做那个判断**。

- [ ] **Step 2: 先造合成黄金夹具（入库）**

创建 `tests/fixtures/ocr_layout_golden.jsonl`（**一行一个 record**，内容为**手写假文本**，无真实规范内容、无凭据）：

```json
{"result": {"layoutParsingResults": [{"prunedResult": {"width": 100, "height": 200, "parsing_res_list": [{"block_label": "paragraph_title", "block_content": "目次", "block_bbox": [10, 10, 40, 20], "block_order": 1, "global_block_id": 0}, {"block_label": "content", "block_content": "前言.....4\n1 总则.....6", "block_bbox": [10, 30, 90, 60], "block_order": 2, "global_block_id": 1}, {"block_label": "number", "block_content": "第1页", "block_bbox": [40, 180, 60, 190], "block_order": null, "global_block_id": 2}]}}]}}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.ocr.layout'`

- [ ] **Step 4: 写实现**

创建 `app/ocr/layout.py`：

```python
"""OCR 版面块（layout）的提取、脱敏、锚定、拼接、落盘与读取。

设计依据：`docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`
§3.2 / §3.3 / §5 / §10.1。

本模块**纯函数 + 文件 IO**：不依赖 OCR 客户端、不碰数据库。因此既可单测，
将来也能让前端通过一个轻端点复用同一份锚定/拼接规则（spec §10.1「规则只写一份」）。

**核心设计（span 方案）**：真源是 OCR 自己产出的 `markdown.text`，**不重建**。
每个块锚定到基准文本的**行区间**；拼接时**未编辑的区间原样取回原行** ⇒
「不编辑即逐字节相等」是**结构上恒真**的，不需要靠对拍去"证明"。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
LAYOUT_FILENAME = "layout.json"
LAYOUT_RAW_FILENAME = "layout.raw.json"

# 与 OCR 请求参数 `markdownIgnoreLabels` 对齐：这些块**不进 markdown**。
# 实测依据：参考夹具里的 `number` 块（"第5页"）出现在 parsing_res_list，
# 但**不出现**在同页的 markdown.text 中。
IGNORE_LABELS = frozenset({
    "header", "header_image", "footer", "footer_image",
    "number", "footnote", "aside_text",
})

# 官网响应里这些字段是**带凭据的签名 URL**。本模块用**白名单式**提取：
# 只取显式列出的字段，天然不会带上它们；这个元组仅供测试断言「确实没出现」。
SIGNED_URL_KEYS = ("inputImage", "preprocessedImages", "outputImages")

_HASH_PREFIX = re.compile(r"^#{1,6}\s+(.*)$")


def extract_layout(records: list[dict]) -> dict:
    """OCR JSONL 记录 → 精简 layout 结构（**已脱敏**，**尚未锚定**）。

    只读 `prunedResult.parsing_res_list` 与 `prunedResult.width|height`。
    `markdown`（只有 text/images）不参与 —— 它是**基准文本**，由调用方另行提供。

    **白名单式**：输出字段全是显式构造的，响应中的签名 URL 不可能被带进来。
    """
    pages: list[dict] = []
    blocks: list[dict] = []

    # ⚠️ 页码必须是**跨 record 的扁平累计序号**，**不能**用 `enumerate(records)` 的下标：
    # 一条 record 可能含多个 `layoutParsingResults`（一页一个元素），用 record 下标会让
    # 同一 record 的第 2..N 页**全部拿到同一个页号** ⇒ 左栏高亮定位到错页，且完全静默。
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
                if not isinstance(b, dict):          # 畸形响应不得让整条链中断
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


def _sorted_blocks(blocks: list[dict]) -> list[dict]:
    """按阅读顺序排序。

    实测：**被忽略的 `number` 块的 `block_order` 为 null**，故不能单拿 order 排序。
    `global_block_id` 逐块递增，是更可靠的主序。

    ⚠️ **键必须落在同一空间**：**不能**"有 gid 用 gid、没有用 `page*100000+order`" ——
    两种键混进同一个整数空间，会把缺 gid 的块**系统性推到某个极端**，顺序静默出错。
    故**只要有一个块缺 gid，整体退化**。
    """
    if all(b.get("global_block_id") is not None for b in blocks):
        return sorted(blocks, key=lambda b: b["global_block_id"])
    logger.warning("部分块缺 global_block_id，整体退化为 (page, order) 排序")
    return sorted(blocks, key=lambda b: (b.get("page") or 0, b.get("order") or 0))


def save_layout(layout: dict, output_dir: str | Path) -> str:
    """落盘（原子写：先写 .tmp 再 replace），返回文件路径。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / LAYOUT_FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def load_layout(output_dir: str | Path) -> dict | None:
    """读 `layout.json`；**缺失 / 损坏 / schema 不符一律返回 None**（调用方据此降级，spec §13）。

    这里刻意不抛：审查页没有可用 layout 时应退化为纯预览，而不是 500。
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


def _is_valid_layout(data: object) -> bool:
    """最小 schema 校验：缺了它，P1 会拿到一个"看着有、其实没法渲染"的 dict。"""
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("version"), int):
        return False
    if not isinstance(data.get("pages"), list) or not isinstance(data.get("blocks"), list):
        return False
    return True
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: PASS（`needs_ref` 那条在无真实夹具时 SKIPPED）

- [ ] **Step 6: pyright + 提交**

```bash
pyright app/ocr/layout.py tests/test_ocr_layout.py
git add app/ocr/layout.py tests/test_ocr_layout.py tests/fixtures/ocr_layout_golden.jsonl
git commit -m "feat: OCR layout 的提取、脱敏、schema 校验与落盘读取"
```

---

### Task 3: 块 ↔ 行区间 的**锚定**与**拼接**（P0 核心）

**Files:**
- Modify: `app/ocr/layout.py`（加 `anchor_blocks` / `splice` / `anchor_stats`）
- Create: `tests/test_layout_anchor.py`

**Interfaces:**
- Consumes: Task 2 的 `extract_layout` / `IGNORE_LABELS`；Task 1 的 `reference.md`
- Produces:
  - `anchor_blocks(base_text: str, blocks: list[dict]) -> tuple[list[dict], dict]`
    → 返回 `(blocks_with_span, stats)`；每个块新增 `start_line` / `end_line`（`None` = 未锚定）
  - `splice(base_text: str, blocks: list[dict], edits: dict[int, str]) -> str`
    → `edits` 是 `{块下标: 新文本}`；**未在 edits 里的块原样取回原行**

- [ ] **Step 1: 写失败测试**

创建 `tests/test_layout_anchor.py`：

```python
"""块 ↔ 行区间的锚定与拼接（spec §10）—— **P0 的硬门禁**。

核心性质：**不编辑 ⇒ 逐字节相等**。它不是"要对拍证明的命题"，而是**结构上恒真**
（拼接对未编辑区间原样取回原行）。真正要守的是**锚定覆盖率**。
"""
import json
from pathlib import Path

import pytest

from app.ocr.layout import IGNORE_LABELS, anchor_blocks, extract_layout, splice

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "ocr_layout_golden.jsonl"
REF_DIR = REPO / "data" / "_ocr_reference"

needs_ref = pytest.mark.skipif(
    not (REF_DIR / "raw.jsonl").is_file() or not (REF_DIR / "reference.md").is_file(),
    reason="缺少真实夹具 data/_ocr_reference/{raw.jsonl,reference.md}",
)


def _blocks_of(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    records = [json.loads(ln) for ln in text.split("\n") if ln.strip()]
    return extract_layout(records)["blocks"]


# ── 拼接：核心不变量 ───────────────────────────────────

def test_splice_with_no_edits_is_byte_identical():
    """★ 不编辑 ⇒ **逐字节相等**（这是整个方案的地基，不是"对拍跑出来的"）"""
    base = "A\n\nB\n\nC"
    blocks = [{"label": "content", "content": "A", "start_line": 0, "end_line": 1},
              {"label": "content", "content": "B", "start_line": 2, "end_line": 3},
              {"label": "content", "content": "C", "start_line": 4, "end_line": 5}]
    assert splice(base, blocks, {}) == base


def test_splice_touches_only_the_edited_block():
    """★ 编辑一个块 ⇒ **只**那一段变，其余字节原样"""
    base = "A\n\nB\n\nC"
    blocks = [{"label": "content", "content": "A", "start_line": 0, "end_line": 1},
              {"label": "content", "content": "B", "start_line": 2, "end_line": 3},
              {"label": "content", "content": "C", "start_line": 4, "end_line": 5}]
    assert splice(base, blocks, {1: "B2"}) == "A\n\nB2\n\nC"
    assert splice(base, blocks, {0: "A2", 2: "C2"}) == "A2\n\nB\n\nC2"


def test_splice_ignores_unanchored_blocks():
    """未锚定的块（start_line is None）不参与拼接，不得把内容插进结果"""
    base = "A\n\nB"
    blocks = [{"label": "content", "content": "A", "start_line": 0, "end_line": 1},
              {"label": "content", "content": "幽灵", "start_line": None, "end_line": None},
              {"label": "content", "content": "B", "start_line": 2, "end_line": 3}]
    assert splice(base, blocks, {}) == base
    assert splice(base, blocks, {1: "不该出现"}) == base


def test_splice_multiline_replacement():
    """替换成多行文本也正确（审查页补写丢失内容时会用到）"""
    base = "A\n\nB"
    blocks = [{"label": "content", "content": "A", "start_line": 0, "end_line": 1},
              {"label": "content", "content": "B", "start_line": 2, "end_line": 3}]
    assert splice(base, blocks, {1: "B1\nB2"}) == "A\n\nB1\nB2"


# ── 锚定 ──────────────────────────────────────────────

def test_anchor_blocks_on_golden():
    """合成黄金夹具：忽略块无 span；其余块全部锚定且区间单调"""
    base = "## 目次\n\n前言.....4\n1 总则.....6"
    blocks = _blocks_of(GOLDEN)
    anchored, stats = anchor_blocks(base, blocks)

    by_label = {b["label"]: b for b in anchored}
    assert by_label["number"]["start_line"] is None, "忽略块不该被锚定"
    assert by_label["paragraph_title"]["start_line"] == 0
    assert by_label["paragraph_title"]["end_line"] == 1
    assert by_label["content"]["start_line"] == 2
    assert by_label["content"]["end_line"] == 4

    assert stats["total"] == 2 and stats["missed"] == 0
    spans = [(b["start_line"], b["end_line"]) for b in anchored if b["start_line"] is not None]
    assert spans == sorted(spans), "区间必须按阅读顺序单调递增"


def test_anchor_tolerates_hash_prefix_and_trailing_whitespace():
    """锚定只做两种归一化：**首行允许原文多一个 `#` 前缀** + **去行尾空白**。

    实测差异：`block_content` 是 `.....4␣␣`，`markdown.text` 是 `.....4␣␣␣␣`。
    """
    base = "## 目次\n\n前言.....4    \n1 总则.....6    "
    blocks = [
        {"label": "paragraph_title", "content": "目次", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 1, "global_block_id": 0},
        {"label": "content", "content": "前言.....4\n1 总则.....6", "page": 1,
         "bbox": [0, 0, 1, 1], "order": 2, "global_block_id": 1},
    ]
    anchored, stats = anchor_blocks(base, blocks)
    assert stats["missed"] == 0
    assert anchored[0]["start_line"] == 0 and anchored[0]["end_line"] == 1
    assert anchored[1]["start_line"] == 2 and anchored[1]["end_line"] == 4
    # 且拼接回来逐字节相等
    assert splice(base, anchored, {}) == base


def test_anchor_reports_missed_without_raising():
    """找不到时**不抛**，记 missed 并给 start_line=None（审查页要能降级，不能 500）"""
    base = "完全不相干的内容"
    blocks = [{"label": "content", "content": "这里没有这块", "page": 1,
               "bbox": [0, 0, 1, 1], "order": 1, "global_block_id": 0}]
    anchored, stats = anchor_blocks(base, blocks)
    assert stats == {"total": 1, "missed": 1, "anchored": 0}
    assert anchored[0]["start_line"] is None
    assert splice(base, anchored, {}) == base


@needs_ref
def test_anchor_real_fixture_covers_every_non_ignored_block():
    """★ **P0 门禁**：真实夹具上，**所有非忽略块都必须锚定成功**（missed == 0）。

    这一条取代了旧方案的「往返对拍」：真正的风险不是"拼回来不等"（那恒等），
    而是"**有块根本没锚上**" —— 那些块在 P1 里会无法定位、无法编辑。
    """
    base = (REF_DIR / "reference.md").read_text(encoding="utf-8")
    blocks = _blocks_of(REF_DIR / "raw.jsonl")
    anchored, stats = anchor_blocks(base, blocks)

    missed_labels = [b["label"] for b in anchored if b["start_line"] is None
                     and b["label"] not in IGNORE_LABELS]
    assert stats["missed"] == 0, (
        f"有 {stats['missed']} 个非忽略块未锚定（共 {stats['total']}）：{missed_labels[:8]}"
    )
    assert splice(base, anchored, {}) == base, "未编辑必须逐字节相等"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py -v`
Expected: FAIL —— `ImportError: cannot import name 'anchor_blocks'`

- [ ] **Step 3: 写实现**

在 `app/ocr/layout.py` **追加**：

```python
def _strip_hash_prefix(line: str) -> str:
    """去掉 `## ` 这类标题前缀，返回其后文本（无前缀则原样）。"""
    m = _HASH_PREFIX.match(line)
    return m.group(1).rstrip() if m else line


def _find_span(lines: list[str], content: str, start_from: int) -> tuple[int, int] | None:
    """在 `lines[start_from:]` 中找一段**连续行**与 `content` 匹配。

    匹配判据（**唯一的两种归一化**，都由实测逼出）：
    - **去行尾空白**后逐行相等（实测：`block_content` 的 `.....4␣␣`
      与 `markdown.text` 的 `.....4␣␣␣␣`）
    - **仅首行**允许原文多一个 `#` 前缀 —— `markdown.text` 会给标题型块加 `## `
      （实测：`paragraph_title` 的 content 是 `目次`，原文是 `## 目次`）

    找不到返回 `None`（**不抛**：审查页要能降级，不能 500）。
    """
    want = [ln.rstrip() for ln in content.split("\n")]
    while want and not want[-1]:          # 忽略块内容末尾的空行
        want.pop()
    if not want:
        return None

    n = len(want)
    first_expects_plain = not want[0].startswith("#")
    for i in range(start_from, len(lines) - n + 1):
        for k, w in enumerate(want):
            got = lines[i + k].rstrip()
            if k == 0 and first_expects_plain:
                # 首行允许原文多一个 # 前缀（两种写法都接受）
                if got == w or _strip_hash_prefix(got) == w:
                    continue
                break
            if got != w:
                break
        else:
            return i, i + n
    return None


def anchor_blocks(base_text: str, blocks: list[dict]) -> tuple[list[dict], dict]:
    """把每个块锚定到**基准文本的行区间**；返回 `(带 span 的 blocks, 统计)`。

    - 区间以**行号**表达（`start_line` / `end_line` 半开）。行粒度足够，因为
      拼装时块之间是以空行分隔的，每个块都从**行首**开始。
    - **贪婪 + 游标**：按阅读顺序推进 `cursor`，避免把同一段重复匹配给后面的块。
    - 忽略块（`IGNORE_LABELS`）**不参与锚定**（`start_line=None`）—— 它们本就不在
      基准文本里，出现在 `parsing_res_list` 只是版面检测的副产品。
    - **未锚定不抛**：记 `missed` 并留 `None`，由调用方决定门禁阈值。
    """
    lines = base_text.split("\n")
    cursor = 0
    total = anchored_n = 0
    out: list[dict] = []

    for b in _sorted_blocks(blocks):
        label = b.get("label") or ""
        if label in IGNORE_LABELS:
            out.append({**b, "start_line": None, "end_line": None})
            continue

        total += 1
        span = _find_span(lines, b.get("content") or "", cursor)
        if span is None:
            out.append({**b, "start_line": None, "end_line": None})
            continue

        s, e = span
        cursor = e
        anchored_n += 1
        out.append({**b, "start_line": s, "end_line": e})

    return out, {"total": total, "anchored": anchored_n, "missed": total - anchored_n}


def splice(base_text: str, blocks: list[dict], edits: dict[int, str]) -> str:
    """按区间拼回文本：**只替换 `edits` 里的块**，其余区间原样取回原行。

    `edits` 的键是 `blocks` 的**下标**（与 `anchor_blocks` 返回的列表同序）。

    ⚠️ 这是本方案的地基：`edits` 为空时等价于 `base_text` 本身（**逐字节**），
    因为未编辑的区间是**原样取回原行**而不是"重新生成"。故"往返保真"不需要证明。
    """
    lines = base_text.split("\n")
    ops = sorted(
        ((b["start_line"], b["end_line"], edits.get(i))
         for i, b in enumerate(blocks) if b.get("start_line") is not None),
        key=lambda t: t[0],
    )

    out: list[str] = []
    cursor = 0
    for s, e, new in ops:
        if s < cursor:
            # 区间重叠说明数据坏了；跳过该块而不是产出错乱文本（静默比乱码好）
            logger.warning("layout 区间重叠（跳过该块）: [%d, %d) 与游标 %d", s, e, cursor)
            continue
        out.extend(lines[cursor:s])
        out.extend(lines[s:e] if new is None else new.split("\n"))
        cursor = e
    out.extend(lines[cursor:])
    return "\n".join(out)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py -v`
Expected: PASS（黄金夹具全绿；真实夹具那条在无夹具时 SKIPPED）

> ⚠️ **若真实夹具那条门禁红（`missed > 0`）—— 停下来报告，不要放宽判据、不要改归一化去凑。**
> 它意味着有些块压根没出现在 `markdown.text` 里，那 P1 就定位不到它们，方案要重新评估。
> 这正是 P0 要拦的情况（spec §18）。

- [ ] **Step 5: pyright + 提交**

```bash
pyright app/ocr/layout.py tests/test_layout_anchor.py
git add app/ocr/layout.py tests/test_layout_anchor.py
git commit -m "feat: 块↔行区间锚定与区间拼接（P0 门禁：不编辑即逐字节相等）"
```

---

### Task 4: 两阶段接入导入流程 + 验收脚本

> **两阶段**（`layout.raw.json` → `layout.json`）的原因：锚定必须以**审查页实际使用的文本**为基准，而那份文本是 `_process_import` 里 `clean_ocr_text` 的产物 —— OCR 客户端看不到它。

**Files:**
- Modify: `app/ocr/layout.py`（加 `save_raw_layout_safely` / `finalize_layout`）
- Modify: `app/ocr/paddle_api.py::_download_markdown`（约 380-408 行）
- Modify: `app/routes/import_routes.py::_process_import`（约 783 行，`clean_ocr_text` 之后）
- Create: `scripts/verify_layout_anchor.py`
- Test: `tests/test_ocr_layout.py`（追加）

**Interfaces:**
- Produces: `save_raw_layout_safely(records, output_dir)` / `finalize_layout(output_dir, base_text) -> dict`（返回统计）

- [ ] **Step 1: 写失败测试**

在 `tests/test_ocr_layout.py` **追加**：

```python
def test_save_raw_layout_safely_swallows_errors(monkeypatch, tmp_path):
    """★ 落盘失败**不得抛出**（spec §5.3） —— 且用**非空** records，
    避免被测函数在调用被 monkeypatch 的 save_layout **之前**就提前 return（那样测试会空转）。
    """
    import app.ocr.layout as mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "save_layout", boom)
    mod.save_raw_layout_safely([{"result": {"layoutParsingResults": [{"prunedResult": {
        "width": 1, "height": 1, "parsing_res_list": [
            {"block_label": "content", "block_content": "x", "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": 0}]}}]}}], str(tmp_path))   # 不抛即通过


def test_save_raw_layout_safely_noop_without_output_dir():
    import app.ocr.layout as mod
    mod.save_raw_layout_safely([], None)


def test_save_raw_layout_safely_skips_when_blocks_empty(tmp_path):
    """★ 空 blocks **不落盘** —— 否则产生「有 layout 但 0 块」，审查页渲染空白页，
    绕过 spec §13 的降级契约（该契约的前提是"没有 layout"）。
    """
    import app.ocr.layout as mod
    mod.save_raw_layout_safely(
        [{"result": {"layoutParsingResults": [{"prunedResult": {"width": 10, "height": 20}}]}}],
        str(tmp_path))
    assert not (tmp_path / mod.LAYOUT_RAW_FILENAME).exists()


def test_save_raw_layout_safely_survives_malformed_blocks(tmp_path):
    """畸形解析结果（含非 dict 的块）不得抛出 —— 覆盖 AttributeError 一类"""
    import app.ocr.layout as mod
    mod.save_raw_layout_safely([{"result": {"layoutParsingResults": [
        {"prunedResult": {"width": 1, "height": 1,
                          "parsing_res_list": ["not-a-dict", None,
                                               {"block_label": "content",
                                                "block_content": "x",
                                                "block_bbox": [0, 0, 1, 1],
                                                "block_order": 1, "global_block_id": 0}]}}]}}],
        str(tmp_path))


def test_finalize_layout_anchors_and_persists(tmp_path):
    """两阶段收尾：消费 raw → 锚定 → 落 layout.json（含 span）"""
    import app.ocr.layout as mod
    mod.save_raw_layout_safely([{"result": {"layoutParsingResults": [{"prunedResult": {
        "width": 1, "height": 1, "parsing_res_list": [
            {"block_label": "content", "block_content": "AAA", "block_bbox": [0, 0, 1, 1],
             "block_order": 1, "global_block_id": 0}]}}]}}], str(tmp_path))

    stats = mod.finalize_layout(str(tmp_path), "AAA\n\nBBB")
    assert stats["missed"] == 0 and stats["anchored"] == 1

    layout = mod.load_layout(tmp_path)
    assert layout["blocks"][0]["start_line"] == 0
    assert layout["blocks"][0]["end_line"] == 1
    assert not (tmp_path / mod.LAYOUT_RAW_FILENAME).exists(), "收尾后应删除 raw"


def test_finalize_layout_noop_without_raw(tmp_path):
    """没有 raw 时不做事、不抛（老任务/其他后端路径）"""
    import app.ocr.layout as mod
    stats = mod.finalize_layout(str(tmp_path), "任意文本")
    assert stats is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v -k "raw or finalize"`
Expected: FAIL —— `AttributeError: module 'app.ocr.layout' has no attribute 'save_raw_layout_safely'`

- [ ] **Step 3: 实现两阶段**

在 `app/ocr/layout.py` **追加**：

```python
def save_raw_layout_safely(records: list[dict], output_dir: str | None) -> None:
    """**第一阶段**：把**未锚定**的块落盘为 `layout.raw.json`；失败只记 WARN、**绝不抛出**。

    为什么分两阶段：锚定必须以**审查页实际使用的文本**为基准，而那份文本由
    `_process_import` 里 `clean_ocr_text` 产出 —— OCR 客户端看不到它。故客户端只落
    "块 + 坐标"，由导入流程在清洗之后完成锚定（`finalize_layout`）。

    ⚠️ **blocks 为空时不落盘**：空 blocks 会产出「有 layout 但 0 块」，审查页据此渲染
    **一张空白页** —— 绕过了 spec §13 的降级契约（前提是"**没有** layout"）。

    ⚠️ 异常**必须收窄**（全局规则 §1.2：禁止无差别捕获）。覆盖真实失败集：
    OSError（磁盘/权限）、KeyError/TypeError/ValueError/AttributeError（响应结构异常）。
    """
    if not output_dir:
        return
    try:
        layout = extract_layout(records)
        if not layout["blocks"]:
            logger.warning(
                "layout 为空（响应可能不含 parsing_res_list），不落盘以免审查页渲染空白页: %s",
                output_dir)
            return
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / LAYOUT_RAW_FILENAME
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as e:
        logger.warning("layout.raw.json 落盘失败（不影响导入）: %s", e)


def finalize_layout(output_dir: str | Path, base_text: str) -> dict | None:
    """**第二阶段**：消费 `layout.raw.json`，锚定到 `base_text`，落盘 `layout.json`。

    返回锚定统计（`total` / `anchored` / `missed`）；无 raw 时返回 `None`（不做事）。
    **失败只记 WARN、绝不抛出** —— 与第一阶段同规（layout 是可选增强）。
    """
    out = Path(output_dir)
    raw_path = out / LAYOUT_RAW_FILENAME
    if not raw_path.is_file():
        return None
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        anchored, stats = anchor_blocks(base_text, raw.get("blocks") or [])
        layout = {"version": LAYOUT_VERSION, "pages": raw.get("pages") or [],
                  "blocks": anchored, "base_len": len(base_text)}
        save_layout(layout, out)
        if stats["missed"]:
            logger.warning("layout 锚定有 %d/%d 个块未命中（这些块在审查页无法定位）: %s",
                           stats["missed"], stats["total"], out)
        return stats
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError,
            TypeError, ValueError, AttributeError) as e:
        logger.warning("layout 锚定失败（不影响导入）: %s", e)
        return None
    finally:
        try:
            raw_path.unlink(missing_ok=True)
        except OSError:
            pass
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -v`
Expected: PASS

- [ ] **Step 5: 接入 `_download_markdown`（第一阶段）**

读当前实现（`app/ocr/paddle_api.py` 约 380-408 行）。它逐行解析 JSONL、拼 `parts`、下载图片。改为**先物化 records，再旁路落 raw**：

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

        # 旁路落**未锚定**的版面块（审查页用）。失败只记 WARN，不中断导入（spec §5.3）。
        save_raw_layout_safely(records, output_dir)

        return "\n".join(parts)
```

文件顶部 import 区加：

```python
from app.ocr.layout import save_raw_layout_safely
```

- [ ] **Step 6: 接入 `_process_import`（第二阶段）**

在 `app/routes/import_routes.py` 的 PDF 分支，**`md_text = clean_ocr_text(md_text)` 之后、`_update_task(..., md_text=md_text, ...)` 之前**插入：

```python
        # 锚定版面块到**审查页实际使用的这份文本**（清洗后的 md_text），
        # 落 layout.json。失败只记 WARN，不影响导入（spec §5.3）。
        finalize_layout(Path(OUTPUT_DIR) / task_id, md_text)
```

并在 import 区加 `from app.ocr.layout import finalize_layout`。

> ⚠️ **位置是承重的**：必须在 `clean_ocr_text` **之后**。放前面会让锚定基准与审查页文本不同源 —— 这正是 `/plan-eng-review` Outside Voice #1 指出的失效。

- [ ] **Step 7: 跑相关增量测试**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py tests/test_layout_anchor.py tests/test_paddle_vl_v2.py tests/test_import.py -v`
Expected: 全 PASS（`test_paddle_vl_v2.py` 覆盖 `_download_markdown` 既有行为，**必须不被破坏**）

- [ ] **Step 8: 写验收脚本（fail-closed）**

创建 `scripts/verify_layout_anchor.py`：

```python
"""P0 门禁验收：对真实产物逐份检查**锚定覆盖率**。

用法：D:/Python/python.exe scripts/verify_layout_anchor.py

扫描 data/outputs/*/ 下**成对**存在 `<name>.md` 与 `layout.json` 的目录。
- 有可检查的产物且全部 `missed == 0` ⇒ 退出码 0
- 有产物但存在未锚定块 ⇒ 退出码 1
- **一份可检查的产物都没有 ⇒ 退出码 2**（fail-closed：没验证过就不算通过）
"""
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.ocr.layout import IGNORE_LABELS, load_layout, splice  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUTPUTS = REPO / "data" / "outputs"


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
        blocks = layout.get("blocks") or []
        missed = [b for b in blocks
                  if b.get("start_line") is None and (b.get("label") or "") not in IGNORE_LABELS]
        base = mds[0].read_text(encoding="utf-8")

        # 未编辑 ⇒ 必须逐字节相等（这是本方案的结构性保证，顺手一并验）
        identical = splice(base, blocks, {}) == base

        if not missed and identical:
            print(f"✅ {d.name}  块 {len(blocks)}，未锚定 0")
        else:
            failed += 1
            print(f"❌ {d.name}  未锚定 {len(missed)}  逐字节相等={identical}"
                  f"  例：{[b.get('label') for b in missed[:5]]}")

    print(f"\n共检查 {checked} 份，失败 {failed} 份")
    if checked == 0:
        print("❌ 没有可校验的成对产物 —— 需先跑一次真实导入产生 layout.json。"
              "（门禁 fail-closed：未验证不算通过）")
        return 2
    return 1 if failed else 0


sys.exit(main())
```

- [ ] **Step 9: 跑验收**

Run: `export PYTHONUTF8=1 && D:/Python/python.exe scripts/verify_layout_anchor.py; echo "退出码=$?"`
Expected: 首次运行打印 `共检查 0 份` + 那句提示，**退出码 = 2**。
**这是预期的**：P0 的最终验收 = 跑一次真实导入后重跑本脚本，应全 ✅ 且退出码 0。

- [ ] **Step 10: pyright + 提交**

```bash
pyright app/ocr/layout.py app/ocr/paddle_api.py app/routes/import_routes.py scripts/verify_layout_anchor.py tests/test_ocr_layout.py
git add app/ocr/layout.py app/ocr/paddle_api.py app/routes/import_routes.py scripts/verify_layout_anchor.py tests/test_ocr_layout.py
git commit -m "feat: 两阶段落盘 layout（清洗后锚定）+ fail-closed 验收脚本"
```

---

## P0 出口判据（全部满足才算通过，spec §18）

- [ ] `test_splice_with_no_edits_is_byte_identical` **PASS** —— 不编辑即逐字节相等（结构性保证）
- [ ] `test_splice_touches_only_the_edited_block` **PASS** —— 只改被编辑的块
- [ ] **真实夹具上 `missed == 0`**（`test_anchor_real_fixture_covers_every_non_ignored_block` **PASS，不是 SKIPPED**）
- [ ] 提取结构**不含**任何签名凭据（合成 + 真实两条断言 PASS）
- [ ] **落盘前脱敏**已生效（`capture_ocr_reference.py` 里内存断言 + 替换）
- [ ] `paddle_api` 既有测试未被破坏（`test_paddle_vl_v2.py` PASS）
- [ ] 跑一次**真实导入**后，`scripts/verify_layout_anchor.py` **全 ✅ 且退出码 0**
- [ ] 增量测试 + `pyright` 无新增 error

> **任一条不满足 ⇒ 不得进入 P1**（spec §18：锚定一旦有偏差，P2 之后所有编辑都会以「**静默改写 `md_text`**」的形式污染导入结果）。

---

## Decision ledger（/plan-eng-review 2026-10-04）

### R1: 评审发现整改范围
Finding: A1 页码索引 · A2 覆盖边界 · A3 空 blocks · Q1 排序键 · Q2 异常范围 · T1–T5 · F1 pyright 调用
Plan baseline: 原计划的实现与验证步骤
Runtime evidence: 实测 `pyright --version` → 1.1.410，`D:/Python/python.exe -m pyright` → `No module named pyright`；实测 `_download_markdown` 仅存在于 `PaddleVLClient`
Question D1: 哪些采纳进 P0 计划
State: approved
Actual answer: 全选（A1 / A2 / A3+Q1+Q2 / T1–T5）
Accepted scope: 已全部改入本计划正文
History: —

### R2: `.md` 上传的审查入口
Finding: 用户提出 —— 保留现有「左 md 源码 / 右预览」设计作为 `.md` 审查页
Plan baseline: spec §2.2 未涵盖此入口
Runtime evidence: `_process_import` 的 `.md` 分支直进 Phase 2（`import_routes.py:749-755`），无 review 环节；`test_upload_markdown` 只断言上传响应，改流程不破
Question D2: Include / Defer / Hold
State: approved
Actual answer: Include
Accepted scope: spec 新增 §20（入口分派 / 清洗次序 / 图片 404 / 与 B 组同批）

### R3: P0 的形态（Outside Voice #6）
Finding: 重建整篇 `markdown.text` 是反推一个未公开渲染器
Plan baseline: 拼装规则（`LABEL_PREFIX` + `IGNORE_LABELS`）+ 往返对拍
Runtime evidence: API 已产出规范 `markdown.text`；Codex 独立复核判定过度耦合
Question D3: span 方案 / 保持现方案 / 先量命中率
State: approved
Actual answer: **改为 span 方案**
Accepted scope: **本计划正文已按 span 方案重写** —— 真源改为原始 `markdown.text`；块只记录行区间；拼接只替换被编辑区间；门禁从「往返对拍」改为「**锚定覆盖率**」。Outside Voice #1–#5 已一并并入。

**Approval readiness: PASS** — R1 (D1 全选) / R2 (D2 Include) / R3 (D3 span)

### Outside Voice 发现的处置

| # | Outside Voice 发现 | 处置 |
| --- | --- | --- |
| 1 | 对拍对象不是审查页真正使用的文本（`clean_ocr_text`） | **成立** ⇒ 已解决：锚定移到 `clean_ocr_text` **之后**（Task 4 Step 6），基准与审查页同源 |
| 2 | P0 门禁 fail-open | **成立** ⇒ 已解决：验收脚本 `checked == 0` **非零退出**（Task 4 Step 8）；补**入库的合成黄金夹具** |
| 3 | 拼装规则未绑定实际 OCR 参数 | **成立** ⇒ 随 R3 消失（不再重建，前缀/忽略表不再是承重件） |
| 4 | 夹具落盘签名凭据（与全局约束自相矛盾） | **成立，且是本计划自相矛盾** ⇒ 已解决：`capture_ocr_reference.py` **内存中判存、落盘前脱敏** |
| 5 | `load_layout`/`save_layout_safely` 契约未被真正覆盖；一条测试**空转** | **成立** ⇒ 已解决：schema 校验 + UTF-8 兜底；测试改传**非空** records；异常元组补 `AttributeError` |
| 6 | 整体重建 `markdown.text` 过度耦合，建议 span 方案 | **采纳** ⇒ R3 |

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | `/plan-eng-review` → Codex | Independent 2nd opinion | 1 | completed | 6 findings |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN | 11 issues, 1 critical gap |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** provider=codex, phase=plan-review, **completed**（stdin 喂入，全文未截断）。6 条发现全部核验为成立；#6 触发架构改向。
- **VERDICT:** ENG REVIEW 未 CLEAR（11 issues / 1 critical gap）—— **整改已全部落入本计划正文（含 span 方案重写），但尚未经第二轮复验**，故 **eng review required**。
  - 问题分解：Architecture 3（A1/A2/A3）· Code Quality 2（Q1/Q2）· Performance 1（P3，附录）· Test gaps 5（T1–T5）。另有 1 处事实纠错（F1，不计数）。
  - **critical gap = A3**：空 blocks 落盘 ⇒ 审查页**静默渲染空白页**，既无测试也无错误处理。已在 Task 4 用「空则不落盘」+ 回归用例覆盖。

NO UNRESOLVED DECISIONS
