# OCR 审查页改版 · P1 只读对照 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让审查者能**左边看原文件、右边看版面块**，两侧**点任一边滚到并高亮另一边**，每个块标出它会被切成哪一条。

**Architecture:** 后端加两个只读端点（`layout` JSON / `page/{n}.png` 页图），把 P0 落下的 `layout.json` 送到前端。前端新建一个有左右两栏的审查页，右栏**点击即编辑入口**留到 P2，本期**只读**。块↔条文的对应**不自己算**：用 `parse_markdown` 的**诊断钩子** `_decisions` 拿逐行判定（parser 自己的尺子），而块的行区间来自 P0 —— **两者同一坐标系**，故对齐退化为一次二分查找。

**Tech Stack:** FastAPI + Jinja2 + Alpine.js + PyMuPDF(`fitz`) + Playwright 探针

**Spec:** `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`（§6 / §7 / §9 / §11 / §13 / §18·P1）

**前置（已交付）**：P0 的 `app/ocr/layout.py`。`outputs/{task_id}/layout.json` 含：
`blocks[]`（`label` / `content` / `page` / `bbox` / `order` / `global_block_id` / **`start_line`** / **`end_line`** / `prefix` / `suffix`）、`pages[]`（`index`/`width`/`height`）、`base_sha256`、`ocr_params`、`stats`。

---

## Global Constraints

1. **`md_text` 对外契约不变** —— `parse_markdown` / `plain_text` / `build_embed_text` 零改动；**不得改 `md_parser.py` 的判定逻辑**（只允许**读**它的诊断钩子）。
2. **不新增依赖** —— 页图用已在用的 PyMuPDF。
3. **不碰真实 `data/`**：测试与探针一律隔离四路径（`DATABASE_PATH`/`UPLOAD_DIR`/`OUTPUT_DIR`/`LANCE_DB_PATH`）+ 独立端口。
4. **本期只读**：不许有编辑/保存/提交路径。**没有**任何请求能改 `md_text`。
5. **降级必须静默可用**：没有 `layout.json`（老任务 / `.md` / 非扫描件 / 其他 OCR 后端）时，PDF 审查页**退回现有 `ocr_review.html`**，不报错。
6. 每个 Task 跑增量测试 + `pyright <显式路径>`（**不得写 `python -m pyright`**，实测报 `No module named pyright`），再提交。
7. 逐个 `git add <文件>`，**不得 `git add .`**。
8. 改 `static/**` 或模板后，`base.html` 里 `?v=N` **必须递增**（否则浏览器用缓存旧 js）。

## P0 实测带来的两条硬约束（本期必须处理）

| # | 事实（2026-10-04，完整 57 页实测） | 本期的处置 |
| --- | --- | --- |
| **A** | **2 个块未锚定**（`table`×1 / `chart`×1，`start_line is None`）：markdown 里的表示与 `block_content` 不同 | **必须显示但明确标注「原文件未定位」**：右栏照常渲染内容，左侧**不画框**、点它**不滚左栏**。**不得静默隐藏** —— 用户会以为这块不存在 |
| **B** | 块↔**条文**的对齐（spec §11）**从未实测** | 用 `parse_markdown(md_text, _decisions=…)` 的**逐行判定**做（parser 自己的尺子）；`clause_no` 取「起始行 ≤ 块起始行的最后一个候选行」。**前导块**（第一个候选行之前）标 `None`（spec §11.2） |

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/ocr/layout.py`（改） | 追加 **`align_clauses(md_text, blocks)`** —— 纯函数，块 → 条文号 |
| `app/routes/import_routes.py`（改） | 追加两个**只读**端点：`GET …/layout`（JSON）、`GET …/page/{n}.png`（页图）；并把 `review_page` 的 PDF 分支切到新模板 |
| `app/templates/partials/ocr_review_blocks.html`（新建） | 左栏原文件 / 右栏版面块的审查页 |
| `static/components/review-blocks.js`（新建） | 该页的 Alpine 组件（缩放 / 拖动 / 高亮 / 双向联动） |
| `static/app.css`（改） | 新页面的样式 |
| `app/templates/base.html`（改） | 引入新脚本（带 `?v=1`），并递增既有资源的版本号 |
| `tests/test_layout_clauses.py`（新建） | `align_clauses` 单测 + 真实夹具实测 |
| `tests/test_review_layout_routes.py`（新建） | 两个端点的契约与越界防护 |
| `scripts/probe_spec_ui.py`（改） | 新增 **f12** 组：左栏页图/缩放/拖动/画框、右栏块列表/类型标签/条文号、双向联动、未锚定块标注 |

**为什么两端点写进 `import_routes.py`**：它们与既有 `/import/review/{task_id}/*` 同域同鉴权、共享 `_get_task` 与 task_id 校验；新建路由模块会重复这套。`align_clauses` 放 `layout.py` 是因为它与 P0 的锚定**同属「layout ↔ markdown 文本结构」**这一件事。

---

### Task 1: 块 ↔ 条文 对齐（`align_clauses`）

**Files:**
- Modify: `app/ocr/layout.py`（追加 `align_clauses`）
- Create: `tests/test_layout_clauses.py`

**Interfaces:**
- Consumes: `app.parser.md_parser.parse_markdown(md_text, *, _decisions=None)`、`_Decision`（`NamedTuple`，字段 `line` / `kind` / `level` / `clause_no` / `parent_key` / `in_commentary` / `stack`）
- Produces: `align_clauses(md_text: str, blocks: list[dict]) -> tuple[list[dict], dict]`
  - 每个块新增 `clause_no: str | None`
  - `stats = {"blocks", "labeled", "unlabeled", "distinct_clauses"}`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_layout_clauses.py`：

```python
"""块 ↔ 条文 对齐（spec §11，计划 Task 1）。

**不自己扫编号**：用 `parse_markdown(..., _decisions=)` 的逐行判定 —— 那是 parser
**自己的**尺子。自己再实现一遍 `_match_clause_line` 会变成「第二把尺子」，
判据一旦与 parser 漂移就**静默错标**（本仓 memory 明确警告过这一类）。
"""
import json
from pathlib import Path

import pytest

from app.ocr.layout import align_clauses, extract_layout

REPO = Path(__file__).resolve().parent.parent
REF_DIR = REPO / "data" / "_ocr_reference"


def _require_ref() -> None:
    if (REF_DIR / "raw.jsonl").is_file() and (REF_DIR / "reference.md").is_file():
        return
    import os
    if os.environ.get("REQUIRE_LAYOUT_FIXTURE") == "1":
        pytest.fail("缺少真实夹具：P0/P1 验收不得空过")
    pytest.skip("缺少真实夹具 data/_ocr_reference/{raw.jsonl,reference.md}")


def _blk(content, start_line, gid):
    return {"label": "text", "content": content, "page": 1, "bbox": [0, 0, 1, 1],
            "order": gid, "global_block_id": gid,
            "start_line": start_line, "end_line": start_line + 1,
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
              {**_blk("", None, 1), "label": "table", "start_line": None, "end_line": None}]
    out, stats = align_clauses(MD, blocks)
    assert out[0]["clause_no"] == "3.0.1"
    assert out[1]["clause_no"] is None
    assert stats["unlabeled"] == 1


def test_multi_line_block_spanning_into_next_clause_keeps_its_start_clause():
    """跨到下一条文里的块（`# 目次` 那种长块）按**起始行**归属，不按结束行"""
    md = "3.0.1 第一条。\n\n3.0.2 第二条。\n\n3.0.3 第三条。"
    blocks = [_blk("第一条。\n\n3.0.2", 0, 0)]
    out, _ = align_clauses(md, blocks)
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


def test_real_fixture_aligns_most_blocks():
    """★ 真实夹具实测：已锚定的块里应当**绝大多数**拿到条文号。

    实测覆盖（2026-10-04）：563 可锚定、561 已锚定；未锚定的 2 个（table/chart）
    必然无条文号。**未标注率超 5% 就说明对齐算法有问题**，不是"数据如此"。
    """
    _require_ref()
    md = (REF_DIR / "reference.md").read_text(encoding="utf-8")
    blocks = extract_layout([json.loads(l) for l in
                             (REF_DIR / "raw.jsonl").read_text(encoding="utf-8").split("\n")
                             if l.strip()])["blocks"]
    # 这里只喂"已锚定"的块（P1 的 layout.json 就是这种形态）
    from app.ocr.layout import anchor_blocks
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_layout_clauses.py -q`
Expected: FAIL —— `ImportError: cannot import name 'align_clauses'`

- [ ] **Step 3: 写实现**

在 `app/ocr/layout.py` **追加**（并在文件顶部的 import 区加 `import bisect`）：

```python
def align_clauses(md_text: str, blocks: list[dict]) -> tuple[list[dict], dict]:
    """给每个块标出它所属的**条文号**（spec §11）；返回 `(blocks, stats)`。

    **为什么用 `parse_markdown` 的诊断钩子而不是自己扫编号**：
    自己实现一遍编号匹配 = 本仓反复警告的「**第二把尺子**」。parser 的判据
    （R14 兄弟表决、内节点判据、同号重复拒绝、目次过滤…）复杂且会演进，
    私有的第二套判据一旦漂移就**静默错标**，而错标比不标更坏。

    `_decisions` 是 parser **自己**逐行输出的判定，且其 `line` 与 P0 锚定的
    `start_line` **同一坐标系**（都是 `md_text.split("\\n")` 的下标）——
    故对齐退化为一次二分查找。

    - 规则：块所属条文 = **起始行 ≤ 块起始行** 的**最后一个** `kind == "cand"` 行
    - 前导块（第一个候选行之前）与未锚定块 ⇒ `None`（spec §11.2：**不猜**）
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
```

并在 import 区加：

```python
from app.parser.md_parser import _Decision, parse_markdown
```

> ⚠️ 从 `md_parser` 导入**私有名** `_Decision` 是刻意的：它是钩子的**公开契约形状**
> （docstring 明确写了「对照断言用」）。若将来它改名，这里会**导入期报错**而不是静默错标 —— 这是想要的失败方式。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_layout_clauses.py -q -s`
Expected: PASS（真实夹具那条会打印覆盖率）

> ⚠️ 若 `unlabeled_rate ≥ 5%` ⇒ **停下来报告**，不要放宽阈值。它意味着已锚定的块
> 拿不到条文号（可能是 `cand` 行判定与锚定坐标系不一致），属于真问题。

- [ ] **Step 5: pyright + 提交**

```bash
pyright app/ocr/layout.py tests/test_layout_clauses.py
git add app/ocr/layout.py tests/test_layout_clauses.py
git commit -m "feat: 块↔条文对齐（用 parser 诊断钩子，不另起第二把尺子）"
```

---

### Task 2: 两个只读端点（layout JSON / 页图 PNG）

**Files:**
- Modify: `app/routes/import_routes.py`（在既有 review 端点旁追加）
- Create: `tests/test_review_layout_routes.py`

**Interfaces:**
- Produces:
  - `GET /import/review/{task_id}/layout` → `{"blocks":[…含 clause_no…], "pages":[…], "stats":{…}}`；无 layout ⇒ `404 {"detail": "无版面数据"}`
  - `GET /import/review/{task_id}/page/{n}.png?scale=<float>` → `image/png`；`scale ∈ [0.25, 3.0]`，越界 422；越界页号 404

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_layout_routes.py`：

```python
"""审查页的两个只读端点（spec §6.1 / §7，计划 Task 2）。"""
import json

import pytest

from app.ocr.layout import LAYOUT_FILENAME, save_layout
from app.routes.import_routes import create_task


@pytest.fixture
def review_task(auth_client, monkeypatch, tmp_path):
    """造一个待审查任务 + 一份最小 layout.json，返回 (client, task_id)。"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.ocr.layout.REPO", tmp_path, raising=False)
    for n in ("lance", "up", "out"):
        (tmp_path / n).mkdir(parents=True, exist_ok=True)
    from app.database import init_db
    init_db()

    task_id = "a1b2c3d4"
    create_task(task_id, owner="t")
    return auth_client, task_id, tmp_path


def test_layout_endpoint_returns_blocks_with_clause_no(review_task, monkeypatch):
    client, task_id, tmp_path = review_task
    out = tmp_path / "out" / task_id
    out.mkdir(parents=True, exist_ok=True)
    save_layout({
        "version": 1,
        "pages": [{"index": 1, "width": 100, "height": 200}],
        "blocks": [{"label": "text", "content": "正文", "page": 1, "bbox": [1, 2, 3, 4],
                    "order": 1, "global_block_id": 0,
                    "start_line": 0, "end_line": 1, "prefix": "", "suffix": "",
                    "clause_no": "3.0.1"}],
        "base_sha256": "x",
    }, out)

    resp = client.get(f"/import/review/{task_id}/layout")
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocks"][0]["clause_no"] == "3.0.1"
    assert body["pages"][0]["index"] == 1


def test_layout_endpoint_404_without_layout(review_task):
    client, task_id, _ = review_task
    resp = client.get(f"/import/review/{task_id}/layout")
    assert resp.status_code == 404


def test_page_endpoint_rejects_out_of_range_scale(review_task):
    """★ `scale` 是**外部输入**：越界必须被挡下（全局规则 §1.1），不得拿去渲染"""
    client, task_id, _ = review_task
    for bad in ("0", "0.1", "3.5", "abc", "-1"):
        r = client.get(f"/import/review/{task_id}/page/1.png?scale={bad}")
        assert r.status_code == 422, f"scale={bad} 应被拒"


def test_page_endpoint_404_for_missing_source(review_task):
    """原文件不存在 ⇒ 404，不是 500"""
    client, task_id, _ = review_task
    assert client.get(f"/import/review/{task_id}/page/1.png").status_code == 404


def test_page_endpoint_404_for_invalid_task_id_format(auth_client):
    """`task_id` 必须是 8 位 hex（防目录拼接越权）"""
    assert auth_client.get("/import/review/ZZZZZZZZ/page/1.png").status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_review_layout_routes.py -q`
Expected: FAIL —— 404（路由不存在）

- [ ] **Step 3: 写实现**

在 `app/routes/import_routes.py` 既有 `review_content` **之后**追加：

```python
@router.get("/import/review/{task_id}/layout")
async def review_layout(task_id: str):
    """把 P0 落下的版面块（含条文号）下发给审查页（**只读**，spec §7）。

    没有 layout.json（老任务 / .md / 非扫描件 / 其他 OCR 后端）⇒ 404，
    前端据此**退回现有源码审查页**（spec §13）。
    """
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)
    layout = load_layout(Path(OUTPUT_DIR) / task_id)
    if layout is None:
        return JSONResponse({"detail": "无版面数据"}, status_code=404)
    return {
        "blocks": layout.get("blocks") or [],
        "pages": layout.get("pages") or [],
        "stats": layout.get("stats") or {},
    }


@router.get("/import/review/{task_id}/page/{page_no}.png")
async def review_page_image(task_id: str, page_no: int, scale: float = 1.5):
    """渲染原 PDF 的某一页为 PNG（spec §6.1）。

    `scale` 是**外部输入**：必须限幅（全局规则 §1.1）—— 否则 200 倍缩放能把
    PyMuPDF 渲染成一堵墙，把服务端内存吃光。
    """
    import fitz
    from fastapi.responses import FileResponse

    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)
    if not 0.25 <= scale <= 3.0:
        return JSONResponse({"detail": "scale 需在 [0.25, 3.0]"}, status_code=422)

    task = _get_task(task_id)
    src = (task or {}).get("file_path") or ""
    src_path = Path(src)
    if not src or not src_path.is_file():
        return JSONResponse({"detail": "原始文件不存在"}, status_code=404)

    try:
        with fitz.open(str(src_path)) as doc:
            if not 1 <= page_no <= len(doc):
                return JSONResponse({"detail": "页号越界"}, status_code=404)
            pix = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(scale, scale))
            out_dir = Path(OUTPUT_DIR) / task_id / "_pages"
            out_dir.mkdir(parents=True, exist_ok=True)
            png = out_dir / f"{page_no}@{scale:.2f}.png"
            if not png.is_file():
                pix.save(str(png))
    except (OSError, RuntimeError) as e:
        logger.warning("页图渲染失败 task=%s page=%s: %s", task_id, page_no, e)
        return JSONResponse({"detail": "页图渲染失败"}, status_code=500)

    return FileResponse(str(png), media_type="image/png")
```

并在 import 区加 `from app.ocr.layout import finalize_layout, load_layout`（把既有那行补上 `load_layout`）。

> ℹ️ 页图按 `(页号, scale)` 落盘缓存 —— 同一档位只渲染一次；P1 不做缓存淘汰
> （缓存目录随任务目录一起被 T2 的存储治理清理，见 spec §19）。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_review_layout_routes.py -q`
Expected: PASS

- [ ] **Step 5: pyright + 提交**

```bash
pyright app/routes/import_routes.py tests/test_review_layout_routes.py
git add app/routes/import_routes.py tests/test_review_layout_routes.py
git commit -m "feat: 审查页只读端点（layout JSON / 页图 PNG，scale 限幅）"
```

---

### Task 3: 新页面骨架 + 左栏原文件（页图 / 4 档缩放 / 拖动 / 高亮框）

**Files:**
- Create: `app/templates/partials/ocr_review_blocks.html`
- Create: `static/components/review-blocks.js`
- Modify: `static/app.css`（追加样式）
- Modify: `app/templates/base.html`（引入新脚本 + 递增版本号）

**Interfaces:**
- Consumes: Task 2 的 `/layout` 与 `/page/{n}.png?scale=`
- Produces（`review-blocks.js` 暴露的 Alpine 组件 `reviewBlocks()`）：
  - 状态：`blocks` / `pages` / `curPage` / `scaleMode`（`"fit"|1.0|1.5|2.0`）/ `zoom`（实际倍数）/ `unlocated`
  - 方法：`init()` / `pageUrl(n)` / `setScaleMode(m)` / `resetZoom()` / `startPan(e)` / `highlightOf(blockIdx)`

- [ ] **Step 1: 写页面模板**

创建 `app/templates/partials/ocr_review_blocks.html`：

```html
<!-- OCR 审查页（版面块 · P1 只读对照） -->
<div x-data="reviewBlocks()" x-init="init()" class="rvb-root">
    <div class="rvb-error" x-show="error" x-cloak>
        <p x-text="error" style="color:#c00"></p>
        <a href="/">返回主界面</a>
    </div>

    <div class="rvb-panes" x-show="!error" x-cloak>
        <!-- 左栏：原文件 -->
        <section class="rvb-left">
            <header class="rvb-bar">
                <span class="rvb-file" x-text="fileName"></span>
                <span class="rvb-pageinfo" x-text="`${curPage} / ${pages.length}`"></span>
            </header>
            <!-- 缩放：4 档 + 重置（spec §6.2） -->
            <div class="rvb-zoom">
                <button type="button" class="outline" @click="setScaleMode('fit')"
                        :class="scaleMode === 'fit' && 'rvb-on'">适应宽度</button>
                <button type="button" class="outline" @click="setScaleMode(1.0)"
                        :class="scaleMode === 1 && 'rvb-on'">100%</button>
                <button type="button" class="outline" @click="setScaleMode(1.5)"
                        :class="scaleMode === 1.5 && 'rvb-on'">150%</button>
                <button type="button" class="outline" @click="setScaleMode(2.0)"
                        :class="scaleMode === 2 && 'rvb-on'">200%</button>
                <button type="button" class="secondary outline" @click="resetZoom()"
                        title="缩放恢复「适应宽度」">重置</button>
            </div>
            <!-- 页图 + 高亮框覆盖层（百分比定位 ⇒ 缩放自动跟随） -->
            <div class="rvb-viewport" x-ref="vp" @mousedown="startPan($event)"
                 @scroll="onScroll()">
                <div class="rvb-canvas" :style="canvasStyle()">
                    <img :src="pageUrl(curPage)" @load="onImgLoad()" alt="原文件第 ${curPage} 页"
                         class="rvb-img" draggable="false">
                    <!-- 版面块高亮框（未定位的块不画） -->
                    <template x-for="(b, i) in blocksOnPage()" :key="'hl' + i">
                        <div class="rvb-box" x-show="hasSpan(b)"
                             :class="activeIdx === b.__i && 'rvb-box--on'"
                             :style="boxStyle(b)" @click.stop="focusBlock(b.__i)"
                             :title="b.clause_no || ''"></div>
                    </template>
                </div>
            </div>
        </section>

        <!-- 右栏：版面块列表 -->
        <section class="rvb-right">
            <div class="rvb-blocks" x-ref="list">
                <template x-for="(b, i) in blocks" :key="'bk' + i">
                    <article class="rvb-block" :class="activeIdx === i && 'rvb-block--on'"
                             :data-block-idx="i" @click="focusBlock(i)">
                        <header class="rvb-block-hd">
                            <span class="rvb-tag" x-text="labelZh(b.label)"></span>
                            <!-- 条文号：无归属则不标（spec §11.2） -->
                            <span class="rvb-clause" x-show="b.clause_no" x-text="b.clause_no"></span>
                            <!-- ⚠️ 未定位必须**明说**，不得静默隐藏 -->
                            <span class="rvb-unlocated" x-show="!hasSpan(b)"
                                  title="该块在 markdown 中的表示与原文件不同，无法定位">原文件未定位</span>
                            <span class="rvb-page" x-text="`p${b.page}`"></span>
                        </header>
                        <div class="rvb-block-bd" x-html="renderBlock(b)"></div>
                    </article>
                </template>
            </div>
        </section>
    </div>
</div>
```

- [ ] **Step 2: 写组件**

创建 `static/components/review-blocks.js`：

```js
/* OCR 审查页（版面块 · P1 只读对照）
 * 依赖：md-render.js（复用 marked+DOMPurify+KaTeX，勿另起渲染路径）
 * 后端契约：/import/review/{task_id}/layout、…/page/{n}.png?scale=
 */
function reviewBlocks() {
    return {
        taskId: '',
        fileName: '',
        blocks: [],
        pages: [],
        curPage: 1,
        scaleMode: 'fit',      // 'fit' | 1 | 1.5 | 2
        zoom: 1,               // 实际倍数（fit 时按容器宽算）
        activeIdx: -1,
        error: '',
        _dragging: false,
        _dragFrom: null,

        async init() {
            this.taskId = window.location.pathname.split('/').pop();
            let data;
            try {
                const resp = await fetch(`/import/review/${this.taskId}/layout`);
                if (!resp.ok) { this.error = '该任务没有版面数据，请用源码审查页。'; return; }
                data = await resp.json();
            } catch (e) {
                this.error = '版面数据加载失败：' + e;
                return;
            }
            this.pages = data.pages || [];
            // __i = 原下标：排序/过滤后仍能定位回 blocks（edits 与联动都用它）
            this.blocks = (data.blocks || []).map((b, i) => ({ ...b, __i: i }));
            this.fileName = (await this._fileName()) || '';
            this.curPage = this.pages.length ? this.pages[0].index : 1;
            this.$nextTick(() => this._applyFit());
        },

        async _fileName() {
            try {
                const r = await fetch(`/import/review/${this.taskId}/content`);
                return r.ok ? (await r.json()).file_name || '' : '';
            } catch { return ''; }
        },

        hasSpan(b) { return b.start_line !== null && b.start_line !== undefined; },

        blocksOnPage() {
            return this.blocks.filter(b => b.page === this.curPage);
        },

        pageUrl(n) {
            const s = this.scaleMode === 'fit' ? this._fitScale() : this.scaleMode;
            return `/import/review/${this.taskId}/page/${n}.png?scale=${s.toFixed(2)}`;
        },

        /** 「适应宽度」的倍数：容器宽 ÷ 页宽（页宽来自 layout.json.pages） */
        _fitScale() {
            const vp = this.$refs.vp;
            const p = this.pages.find(x => x.index === this.curPage);
            if (!vp || !p || !p.width) return 1;
            return Math.max(0.25, Math.min(3.0, vp.clientWidth / p.width));
        },

        _applyFit() { if (this.scaleMode === 'fit') this.zoom = this._fitScale(); },

        setScaleMode(m) {
            this.scaleMode = m;
            this.zoom = m === 'fit' ? this._fitScale() : m;
            this.$nextTick(() => this._applyFit());
        },

        /** 「重置」= 回到「适应宽度」（**不是刷新**，spec §6.2） */
        resetZoom() { this.setScaleMode('fit'); },

        canvasStyle() {
            const p = this.pages.find(x => x.index === this.curPage);
            const w = p && p.width ? p.width * (this.zoom || 1) : null;
            return w ? `width:${w}px` : '';
        },

        onImgLoad() { /* 图加载后布局可能变，fit 档重算一次 */ this._applyFit(); },
        onScroll() { /* P1 不需要按滚动做事；留钩子给 P2 的按需渲染 */ },

        /** 高亮框：bbox 按**百分比**定位 ⇒ 缩放自动跟随（spec §6.3） */
        boxStyle(b) {
            const p = this.pages.find(x => x.index === b.page);
            if (!p || !p.width || !p.height || !Array.isArray(b.bbox)) return 'display:none';
            const [x0, y0, x1, y1] = b.bbox;
            return `left:${(x0 / p.width) * 100}%;top:${(y0 / p.height) * 100}%;`
                 + `width:${((x1 - x0) / p.width) * 100}%;height:${((y1 - y0) / p.height) * 100}%`;
        },

        /** 拖动查看被遮盖内容：只在**无版面块**处触发（spec §6.2） */
        startPan(e) {
            if (e.target.closest('.rvb-box')) return;   // 块上不拖（点击留给联动）
            const vp = this.$refs.vp;
            this._dragging = true;
            this._dragFrom = { x: e.clientX, y: e.clientY, sl: vp.scrollLeft, st: vp.scrollTop };
            const move = (ev) => {
                if (!this._dragging) return;
                vp.scrollLeft = this._dragFrom.sl - (ev.clientX - this._dragFrom.x);
                vp.scrollTop = this._dragFrom.st - (ev.clientY - this._dragFrom.y);
            };
            const up = () => {
                this._dragging = false;
                window.removeEventListener('mousemove', move);
                window.removeEventListener('mouseup', up);
            };
            window.addEventListener('mousemove', move);
            window.addEventListener('mouseup', up);
        },

        labelZh(label) {
            return ({ doc_title: '文档标题', paragraph_title: '段落标题', text: '正文',
                      content: '正文', table: '表格', image: '图片', figure_title: '图题',
                      display_formula: '公式', formula_number: '公式编号', chart: '图表',
                      number: '页码', vision_footnote: '脚注' })[label] || label;
        },

        renderBlock(b) {
            try { return window.mdRender.renderHtml(b.content || '', '/import/review/' + this.taskId + '/'); }
            catch (e) { return '<p style="color:#c00">渲染错误</p>'; }
        },

        /** ★ 双向联动：两侧**同一块**、同一视觉样式（spec §9） */
        focusBlock(idx) {
            const b = this.blocks[idx];
            if (!b) return;
            this.activeIdx = idx;
            if (b.page && b.page !== this.curPage) this.curPage = b.page;   // 跨页自动翻
            this.$nextTick(() => {
                if (this.hasSpan(b)) this._scrollLeftTo(b);
                this._scrollRightTo(idx);
            });
        },

        _scrollLeftTo(b) {
            const vp = this.$refs.vp, box = vp && vp.querySelector('.rvb-box--on');
            if (box) box.scrollIntoView({ block: 'center', behavior: 'auto' });
        },

        _scrollRightTo(idx) {
            const el = this.$refs.list && this.$refs.list.querySelector(`[data-block-idx="${idx}"]`);
            if (el) el.scrollIntoView({ block: 'nearest', behavior: 'auto' });
        },
    };
}
```

- [ ] **Step 3: 样式与引入**

在 `static/app.css` 末尾追加：

```css
/* ── OCR 审查页（版面块 · P1）──────────────────────── */
.rvb-root { height: 100%; display: flex; flex-direction: column; }
.rvb-panes { display: flex; gap: 12px; flex: 1; min-height: 0; }
.rvb-left, .rvb-right { flex: 1 1 50%; min-width: 0; display: flex; flex-direction: column; }
.rvb-bar, .rvb-zoom { display: flex; gap: 8px; align-items: center; padding: 4px 0; }
.rvb-zoom button { margin: 0; font-size: 0.8rem; padding: 2px 8px; }
.rvb-zoom .rvb-on { border-color: var(--pico-primary); color: var(--pico-primary); }
.rvb-viewport { flex: 1; overflow: auto; border: 1px solid var(--pico-muted-border-color);
                position: relative; cursor: grab; }
.rvb-viewport:active { cursor: grabbing; }
.rvb-canvas { position: relative; margin: 0 auto; }
.rvb-img { display: block; width: 100%; user-select: none; }
.rvb-box { position: absolute; border: 1.5px solid #4c8bf5; background: rgba(76,139,245,.12);
           cursor: pointer; }
.rvb-box--on { border-color: #e8710a; background: rgba(232,113,10,.18); }
.rvb-blocks { flex: 1; overflow: auto; }
.rvb-block { border: 1px solid var(--pico-muted-border-color); border-radius: 6px;
             padding: 6px 10px; margin-bottom: 8px; cursor: pointer; }
.rvb-block--on { border-color: #e8710a; box-shadow: 0 0 0 1px #e8710a inset; }
.rvb-block-hd { display: flex; gap: 8px; align-items: baseline; font-size: 0.75rem;
                color: var(--pico-muted-color); }
.rvb-tag { background: var(--pico-muted-border-color); border-radius: 3px; padding: 0 6px; }
.rvb-clause { color: var(--pico-primary); font-weight: 600; }
.rvb-unlocated { color: #c08552; }
.rvb-block-bd { font-size: 0.9rem; }
```

在 `app/templates/base.html`：把 `app.css?v=37` → **`app.css?v=38`**，并在 `import.js` 那行**之后**加：

```html
    <!-- 版面块审查页（P1 只读对照）。与 import.js 分开：只在块审查页有意义 -->
    <script src="/static/components/review-blocks.js?v=1"></script>
```

- [ ] **Step 4: 手工验证（探针在 Task 5）**

Run（隔离实例见 Task 5 Step 1 的配方）：
```bash
# 用一份带 layout.json 的真实产物开审查页
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8123/static/components/review-blocks.js?v=1"
```
Expected: `200`

- [ ] **Step 5: 提交**

```bash
git add app/templates/partials/ocr_review_blocks.html static/components/review-blocks.js static/app.css app/templates/base.html
git commit -m "feat: 版面块审查页骨架与左栏原文件（页图/4档缩放/拖动/高亮框）"
```

---

### Task 4: 右栏块列表 + 条文号 + 未定位块标注

> 本 Task 的**代码已在 Task 3 一并写出**（同一个模板与组件文件）。这里把它拆出来，
> 是为了**先只验左栏、再验右栏**，避免一个 Task 里混两类失败。

**Files:**
- Test: `scripts/probe_spec_ui.py`（新增 f12 组的前三条）

**Interfaces:**
- Consumes: Task 3 的 `reviewBlocks()` 与模板

- [ ] **Step 1: 写探针（f12 前三条）**

在 `scripts/probe_spec_ui.py` 末尾追加。**先读这条说明，它决定了探针为什么这么写**：

> ⚠️ **`.md`/非扫描件上传拿不到 layout.json** —— 它们走 `extract_text`，**不跑 OCR**
> （P0 的产物覆盖边界表已写明）。而 `_make_review_task` 上传的是**文本 PDF**
> （`is_scanned()` 为假）⇒ 它拿不到 layout。
>
> ⇒ 探针**不调 OCR**，而是：① 用 `_make_review_task` 造一个真任务；
> ② 把它 2 页的 PDF 尺寸与**参考夹具**拼成一份 layout.json 写进 `OUTPUT_DIR/{task_id}`；
> ③ 同时把该任务的 `md_text` 覆盖成夹具的 md —— 保证 **md 文本 / 块行区间 / 条文号**
> 三者自洽（前两者不一致时，P2 的指纹校验会挡，P1 虽不校验但探针要诚实）。
>
> 夹具在 `data/_ocr_reference/`（不入库）；缺则整组**跳过**。

```python
# ═══════════════════════════════════════════
# f12：版面块审查页（P1 只读对照）
# ═══════════════════════════════════════════

_RVB = ".rvb-root"
_RVB_PAGES = 2          # 探针造 2 页 PDF ⇒ 能验「跨页联动」


def _rvb_make_pdf(path, pages=_RVB_PAGES):
    """造一个 N 页文本 PDF（≥100 字符 ⇒ is_scanned 为假 ⇒ 走 extract_text，不调 OCR）"""
    import fitz
    doc = fitz.open()
    for i in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)      # A4 @72dpi
        page.insert_text((72, 100), f"Probe page {i} " + "x" * 200)
    doc.save(str(path))
    doc.close()


def _rvb_seed(page, suffix):
    """造一个**带 layout.json** 的待审查任务并打开审查页；不成立则返回 None。"""
    import json as _json
    from pathlib import Path as _Path
    from app.config import OUTPUT_DIR
    from app.database import get_db
    from app.ocr.layout import (align_clauses, anchor_blocks, base_sha256,
                                extract_layout, save_layout)

    repo = _Path(__file__).resolve().parent.parent
    ref = repo / "data" / "_ocr_reference"
    if not (ref / "raw.jsonl").is_file():
        return None

    md = (ref / "reference.md").read_text(encoding="utf-8")
    blocks = extract_layout([_json.loads(l) for l in
                             (ref / "raw.jsonl").read_text(encoding="utf-8").split("\n")
                             if l.strip()])["blocks"]
    anchored, _ = anchor_blocks(md, blocks)
    aligned, _ = align_clauses(md, anchored)
    # 只保留前 _RVB_PAGES 页的块 —— 与探针 PDF 的页数对齐，否则点第 2 页的块会 404
    aligned = [b for b in aligned if (b.get("page") or 1) <= _RVB_PAGES]

    task_id = _make_review_task(page, f"PROBE-RVB {suffix}", f"探针块审查 {suffix}")
    # 让任务的 md_text 与夹具一致（块行区间/条文号都基于这份文本）
    with get_db() as conn:
        conn.execute("UPDATE import_tasks SET md_text=?, file_name=? WHERE task_id=?",
                     (md, "probe_block.pdf", task_id))

    out = _Path(OUTPUT_DIR) / task_id
    out.mkdir(parents=True, exist_ok=True)
    save_layout({
        "version": 1,
        "pages": [{"index": i, "width": 595, "height": 842}
                  for i in range(1, _RVB_PAGES + 1)],
        "blocks": aligned,
        "base_sha256": base_sha256(md),
        "stats": {"anchorable": len(aligned), "anchored": len(aligned), "missed": 0},
    }, out)

    page.goto(f"{BASE}/import/review/{task_id}")
    assert poll_until(page, lambda: page.locator(_RVB).count() > 0, 8000), \
        "审查页未渲染出版面块组件（组件或端点有问题）"
    assert poll_until(page, lambda: page.locator(".rvb-block").count() > 0, 8000), \
        "块列表为空"
    return task_id


def f12_1_left_shows_page_image(page):
    """左栏渲染出页图，且尺寸为正（**前置**：没有图后面全是空跑）"""
    assert _rvb_seed(page, "1") is not None, "前置不成立：缺少参考夹具"
    img = page.locator(".rvb-img").first
    assert img.count() == 1, "左栏没有页图"
    box = img.bounding_box()
    assert box and box["width"] > 50 and box["height"] > 50, f"页图尺寸异常：{box}"


def f12_2_zoom_modes_change_zoom_and_reset_returns_to_fit(page):
    """4 档缩放各生效；**「重置」回到适应宽度**（不是刷新 —— spec §6.2 明确）"""
    assert _rvb_seed(page, "2") is not None, "前置不成立"
    fit_w = page.evaluate("() => document.querySelector('.rvb-canvas').offsetWidth")
    page.click("button:has-text('200%')")
    assert poll_until(page, lambda: page.evaluate(
        "() => document.querySelector('.rvb-canvas').offsetWidth") > fit_w * 1.5, 5000), \
        "点 200% 后画布没变宽"
    w200 = page.evaluate("() => document.querySelector('.rvb-canvas').offsetWidth")
    page.click("button:has-text('重置')")
    assert poll_until(page, lambda: abs(page.evaluate(
        "() => document.querySelector('.rvb-canvas').offsetWidth") - fit_w) < 5, 5000), \
        f"「重置」未回到适应宽度（200% 时 {w200}，fit {fit_w}）"


def f12_3_right_lists_blocks_with_type_and_clause(page):
    """右栏列出块、每块有**类型标签**；有归属的块标出**条文号**"""
    assert _rvb_seed(page, "3") is not None, "前置不成立"
    n = page.locator(".rvb-block").count()
    assert n > 20, f"块列表条数异常：{n}"
    assert page.locator(".rvb-block .rvb-tag").count() == n, "有块缺类型标签"
    labeled = page.locator(".rvb-block .rvb-clause").count()
    assert labeled > n * 0.5, f"只有 {labeled}/{n} 块标出条文号 ⇒ 对齐可能没接上"


CASES["f12"] = [f12_1_left_shows_page_image,
                f12_2_zoom_modes_change_zoom_and_reset_returns_to_fit,
                f12_3_right_lists_blocks_with_type_and_clause]
```

> ℹ️ `_rvb_make_pdf` 造 PDF 后，`_make_review_task` 上传的仍是**它自己那个**
> 单页探针 PDF。**若页数对不上**（左栏只有 1 页而 layout 声明 2 页），
> 跨页用例会 404 ⇒ 实现时先跑 f12_1 看左栏页图是否正常，再决定是否让
> `_make_review_task` 改用 `_rvb_make_pdf` 造的文件（那需要改它的上传源）。
> **这是本 Task 唯一需要现场判断的点**，其余照抄。

- [ ] **Step 2: 跑探针（f12）**

Run:
```bash
# 隔离实例配方（四路径 + 独立端口）
D:/Python/python.exe scripts/probe_spec_ui.py f12
```
Expected: `== f12: 3/3 passed ==`

- [ ] **Step 3: 提交**

```bash
git add scripts/probe_spec_ui.py
git commit -m "test: f12 探针（左栏页图/缩重重置、右栏块列表/类型/条文号）"
```

---

### Task 5: 双向联动 + 未定位块标注 + PDF 分支切换与降级

**Files:**
- Test: `scripts/probe_spec_ui.py`（f12 后四条）
- Modify: `app/routes/import_routes.py`（`review_page` 的 PDF 分支切模板）

**Interfaces:**
- Consumes: Task 3 的 `focusBlock` / `hasSpan`

- [ ] **Step 1: 写探针（后四条）**

在 `CASES["f12"]` **之前**插入，并把它们加进该列表：

```python
def f12_4_clicking_block_highlights_it_on_the_left(page):
    """★ 双向联动（右→左）：点右栏块 ⇒ 右栏高亮 + 左栏出现**唯一**对应高亮框"""
    assert _rvb_seed(page, "4") is not None, "前置不成立"
    target = page.evaluate("""() => {
        const els = [...document.querySelectorAll('.rvb-block')];
        return els.findIndex(el => !el.querySelector('.rvb-unlocated'));
    }""")
    assert target >= 0, "找不到已定位的块"
    page.locator(".rvb-block").nth(target).click()

    assert poll_until(page, lambda: page.locator(".rvb-block--on").count() == 1, 5000), \
        "点块后右栏没有高亮"
    assert page.locator(".rvb-box--on").count() == 1, "点块后左栏没有对应高亮框"


def f12_5_clicking_box_scrolls_right_panel_to_that_block(page):
    """★ 双向联动（左→右）：点左栏框 ⇒ 右栏滚到该块并高亮。
    两侧体验**统一**（用户明确要求）。"""
    assert _rvb_seed(page, "5") is not None, "前置不成立"
    n = page.locator(".rvb-block").count()
    page.locator(".rvb-block").nth(n - 1).click()          # 先把右栏滚下去
    page.locator(".rvb-box").first.click()                 # 再点左栏第一个框
    assert poll_until(page, lambda: page.evaluate("""() => {
        const on = document.querySelector('.rvb-block--on');
        if (!on) return false;
        const list = document.querySelector('.rvb-blocks');
        return on.offsetTop >= list.scrollTop - 5
            && on.offsetTop + on.offsetHeight <= list.scrollTop + list.clientHeight + 5;
    }"""), 5000), "点左栏框后，右栏未把对应块滚进视野"


def f12_6_unlocated_block_is_shown_and_labelled_not_hidden(page):
    """★ **未定位块必须显示且明说**（P0 实测：table/chart 各 1 个锚不上）。

    静默隐藏会让用户以为"这块不存在"；标出来他才知道要去原文件里手动核。
    """
    assert _rvb_seed(page, "6") is not None, "前置不成立"
    assert page.locator(".rvb-unlocated").count() >= 1, \
        "夹具里应有未定位的块（table/chart），却一个都没标"
    idx = page.evaluate("""() => {
        const els = [...document.querySelectorAll('.rvb-block')];
        return els.findIndex(el => el.querySelector('.rvb-unlocated'));
    }""")
    body = page.locator(".rvb-block").nth(idx).locator(".rvb-block-bd").inner_text()
    assert body.strip(), "未定位块的内容是空的 —— 应照常渲染，只是不画框"


def f12_7_review_page_falls_back_when_no_layout(page):
    """★ 降级：没有 layout.json 时**不能崩**（spec §13）。

    老任务 / `.md` / 非扫描件都没有 layout ⇒ 审查页必须仍可用。
    `_make_review_task` 上传的是文本 PDF（`extract_text` 路径）⇒ **天生没有 layout**，
    正是本用例要的现场。
    """
    task_id = _make_review_task(page, "PROBE-RVB 7", "探针无版面数据")
    page.goto(f"{BASE}/import/review/{task_id}")
    ok = poll_until(page, lambda: (
        page.locator(".rvb-error").count() > 0
        or page.locator(".review-editor").count() > 0), 8000)
    assert ok, "无 layout 时审查页既没提示也没回退 —— 直接崩了"
```

- [ ] **Step 1b: 把 f12 注册成 7 条（覆盖 Task 4 写的那行）**

把 Task 4 Step 1 末尾的 `CASES["f12"] = [...]` 整行替换为：

```python
CASES["f12"] = [f12_1_left_shows_page_image,
                f12_2_zoom_modes_change_zoom_and_reset_returns_to_fit,
                f12_3_right_lists_blocks_with_type_and_clause,
                f12_4_clicking_block_highlights_it_on_the_left,
                f12_5_clicking_box_scrolls_right_panel_to_that_block,
                f12_6_unlocated_block_is_shown_and_labelled_not_hidden,
                f12_7_review_page_falls_back_when_no_layout]
```

- [ ] **Step 2: 跑探针（f12 全组）**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f12`
Expected: `== f12: 7/7 passed ==`

- [ ] **Step 3: 切换 PDF 分支**

把 `review_page` 里那行 `center_content` 改成三元（`.md` **保持不变**）：

```python
    # 入口分派：单一判据 = 文件名扩展名（spec §20.2）。
    #   `.md` → 源码审查页（左 md 源码 / 右预览）—— **本行不要动**
    #   其他  → 版面块审查页（左原文件 / 右块列表）；无 layout 时该页自行提示降级
    is_md = str(task.get("file_name") or "").lower().endswith(".md")
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": ("partials/ocr_review.html" if is_md
                           else "partials/ocr_review_blocks.html"),
        "task_id": task_id,
        "hide_tree": True,
    })
```

- [ ] **Step 4: 跑相关增量测试**

Run: `D:/Python/python.exe -m pytest tests/test_review_layout_routes.py tests/test_import.py tests/test_layout_clauses.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/probe_spec_ui.py app/routes/import_routes.py
git commit -m "feat: 双向联动 + 未定位块明示 + PDF 分支切到版面块审查页"
```

---

## P1 出口判据（全部满足才算通过，spec §18）

- [ ] `tests/test_layout_clauses.py` 全绿；真实夹具**未标注率 < 5%** 并打印实测值
- [ ] `tests/test_review_layout_routes.py` 全绿（含 `scale` 越界 422、非法 task_id 404、无原文件 404）
- [ ] **探针 f12 7/7**，且**双向联动两条都在**（右→左、左→右）
- [ ] **未定位块**在右栏可见、有「原文件未定位」标注、内容照常渲染、左栏不画框
- [ ] **降级**：无 layout.json 时审查页不崩（探针 f12_7）
- [ ] `.md` 入口**未受影响**（`tests/test_import.py` 的 `test_md_upload_pauses_for_review` 仍 PASS）
- [ ] 增量测试 + `pyright` 无新增 error

> **任一条不满足 ⇒ 不得进入 P2**（P2 会在这一页上加编辑与提交，地基歪了会静默改写 `md_text`）。

---

## NOT in scope（P1 不做）

| 不做 | 理由 |
| --- | --- |
| 任何编辑 / 保存 / 提交路径 | P1 是**只读对照**（spec §18）；P2 才加编辑，且必须与 `splice` 的保真一起上 |
| 条文号在块上的**联动高亮**（点条文号筛出全部同条块） | spec 未要求；YAGNI |
| 左栏**文字选中/复制** | 页图是位图，做不到（spec §6 已确认；扫描件即使换 pdf.js 也无文本层） |
| 页图缓存的淘汰策略 | 随任务目录被 T35 存储治理一并清理 |
| 切分预览（spec §11.4） | 它是**编辑**的验收工具，P2 才有意义 |

## 与既有约定的关系

- 复用 `md-render.js` 渲染块内容 ⇒ DOMPurify/KaTeX 安全边界不变
- 复用 `_get_task` / `LAYOUT_FILENAME` / `load_layout` ⇒ 无重复实现
- **未改** `md_parser.py` 的判定逻辑，只**读**它的诊断钩子
- **未改** `ocr_review.html`（`.md` 的最终归宿）
- 新增静态资源 ⇒ `base.html` 已递增 `app.css?v` 并新增 `review-blocks.js?v=1`
