# 审查页 P2.5 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让审查页的「条文号」从「代码里存在但从未生效」变成真的显示、真的能点；修掉 parser 把标准号正文误判成条号的缺陷；把切分预览从「右栏下部横条」改成三栏之一。

**Architecture:** 分三阶段推进，风险从低到高：**阶段一（Task 1-3）** 全是「让已有能力生效」的接线与单行改动，当天可验证；**阶段二（Task 4-5）** 动 parser 这条承重判据，走全门禁 + 数据重导；**阶段三（Task 6-7）** 做布局。阶段一必须先于阶段二完成，否则 parser 造的伪条会先被显示到界面上。

**Tech Stack:** FastAPI + Jinja2 模板 + Alpine.js 3（无构建步骤）+ Pico CSS；PyMuPDF 渲染页图；pytest；Playwright 探针（`scripts/probe_spec_ui.py`）。

**Spec:** `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`（§6.1 页图缩放 / §11.2 条文号归属 / §11.4 切分预览）+ `TODOS.md` 的 **T38 / T39**。本计划的「诊断依据」章记录了本次四个问题的实测结论，作为 spec 未覆盖部分的补充裁定 —— **spec §11.4 对预览的视觉形态只写了「编辑区旁」四个字，本计划把形态钉死**。

---

## 诊断依据（本次实测，非推测）

| 问题 | 根因 | 判决证据 |
|---|---|---|
| 标题栏看不见条号 | `align_clauses`（`app/ocr/layout.py:510`）**是死代码**：全仓 `app/` 零调用 | `e9915ba8/layout.json` 129 块，含 `clause_no` 字段的 **0** 个 |
| 点条号不跳转 | 同上 ⇒ `b.clause_no` 恒 `undefined` ⇒ `findIndex` 恒 -1 | `static/components/review-blocks.js:443` |
| 新增条号后面板折叠，刷新后正常 | `:key="'pc' + c.clause_no"` 撞上**重复** clause_no，Alpine keyed diff 在列表变更时崩 | 真数据预览列表含 `'GB5768'` **×3** |
| 「1 范围」不在切分列表 | `md_parser.py:26` 的字母编号模式**点号可选** ⇒ 正文行「GB 5768 的…」被吃成条号 `GB5768`，抢占「1」的正文 ⇒ 「1」被判内节点而丢弃 | 对照实验：删掉该行 ⇒ `('1', '本部分适用于…')`；保留 ⇒ `('GB5768', '的本部分规定了…')` |

**生产污染量化：** `clauses` 表中该形态的伪条 **8 条**（`GB6095` / `JGJ46` / `JGJ311` / `HPB235` ← 钢筋牌号 / `JTG/T3610-2019` / `JTG/T 3650-2020` / `GB5725` / `GB 50086 - 2015`），分布在 **4 本规范（共 9 本）**。

**候选修法已实测（Task 4 执行前的事实）：** 把 `md_parser.py:26` 的点号从可选改为必填后，
`pytest tests/test_md_parser.py tests/test_parse_conservation.py` ⇒ **126 passed**；
真 md（`data/outputs/e9915ba8/e9915ba8.md`）伪条 `GB5768` 消失、「1」回归、并救回被吞的「前言」：
`21 条（含 3 伪条）` → `20 条（0 伪条）`。**该实验已还原，未提交。**

---

## Global Constraints

来源：项目 `CLAUDE.md` 与 memory（每条都是本仓踩过的坑，执行时**不得**绕过）。

- **TDD**：先写失败用例再写实现；每个 Task 提交前必须跑**本次变更关联的增量测试**并跑 `pyright app tests scripts`，**不得新增 error**。提交信息 `type: 描述`，**单 Task 单提交**，`type ∈ {feat, fix, test, docs, refactor, chore}`。
- **永不 `git add .`**：一律逐个文件 `git add <路径>`（工作区常有其他会话的改动）。
- **静态资源版本号**：任何 Task 改了 `static/` 下的 js/css（或 `app/templates/partials/*.html` 里引用的资源），**必须递增** `app/templates/base.html` 里对应的 `?v=N`。`review-blocks.js` 现为 `?v=7`、`app.css` 现为 `?v=43`、`md-inline.js` 现为 `?v=1`。改完提示用户 **Ctrl+F5 强刷**。
- **探针运行方式**：`PYTHONPATH` 下 `D:/Python/python.exe scripts/probe_spec_ui.py f13`。四个隔离路径 —— `DATABASE_PATH` / `UPLOAD_DIR` / `OUTPUT_DIR` / `LANCE_DB_PATH` —— **必须同时对 uvicorn 服务端与探针进程导出**；只导给服务端会让夹具写进真实 `data/`，症状伪装成「块列表为空」（已实测否证）。服务端口 **8123**，**不加 `--reload`**。每轮回归前重新 `cp` 一份隔离库。
- **探针证明力受限（T37）**：runner 无逐例 try/except，一例失败即中止整组 ⇒ 一次变异运行只能证明「被点名的用例红 + 它**之前**仍绿」。凡「只 X 红」的措辞都要读作「X 之后未被覆盖」。
- **parser 改动门禁（memory §五，非可选）**：`tests/test_md_parser.py`（120 用例）+ `tests/test_parse_conservation.py`（正文守恒）+ `scripts/survey_structure.py` 的 12 项结构指标 + 两份真语料（`tests/fixtures/cjj2_source.md` 固定夹具 **sha256 勿改**；`data/outputs/aa96b73a/aa96b73a.md` 缺则跳过）。
- **`finalize_layout` 定义在 `align_clauses` 之前**（`:462` vs `:510`）：Python 在调用时解析模块级名字，**该顺序是对的，不要为了「看起来更顺」把函数搬家** —— 搬家会改动 `tests/test_ocr_layout.py` 依赖的导入面。
- **不主动安装依赖**；不改系统配置。

---

## File Structure

| 文件 | 职责 | 本计划中的改动 |
|---|---|---|
| `app/ocr/layout.py` | 版面锚定 + 块→条文号对齐 | 新增 `_align_or_degrade`；`finalize_layout` 接线 |
| `app/parser/md_parser.py` | 切分器（承重判据） | `:26` 字母编号模式点号必填 |
| `app/routes/import_routes.py` | 页图端点 | `:1276` `scale` 上限 3.0 → 8.0 |
| `static/components/review-blocks.js` | 审查页 Alpine 组件 | 页图带 dpr；条号点击跳转；当前块条号高亮；预览栏折叠 |
| `app/templates/partials/ocr_review_blocks.html` | 审查页模板 | `:key` 改下标；切分面板移出 `.rvb-right` 成第三栏 |
| `static/app.css` | 样式 | `.rvb-preview` 去横条形态；三栏比例；折叠态 |
| `app/templates/base.html` | 静态资源版本 | 递增 `?v=N` |
| `scripts/probe_spec_ui.py` | f13 探针组 | 新增 4 个用例 |
| `TODOS.md` | 遗留登记 | 关闭 T38/T39 的已完成部分 |
| `tests/test_ocr_layout.py` | 锚定/落盘测试 | Task 1 新增 2 例 |
| `tests/test_md_parser.py` | 切分器测试 | Task 4 新增 3 例 |
| `tests/test_review_layout_routes.py` | 布局/页图路由测试 | Task 3 新增 1 例 |

---

## 阶段一：让已有能力生效（低风险，当天可验证）

### Task 1: 接线 `align_clauses` —— 让 layout.json 真的带条文号

**Files:**
- Modify: `app/ocr/layout.py:481-489`（`finalize_layout` 体内）
- Test: `tests/test_ocr_layout.py`（文件末尾追加）

**Interfaces:**
- Consumes: `anchor_blocks(base_text, records, ignore) -> (blocks, stats)`（既有）、`align_clauses(md_text, blocks) -> (blocks, stats)`（既有，`:510`）
- Produces: `layout.json` 的每个 block 多出 `clause_no: str | None` 字段；`layout.json.stats` 多出 `clause_blocks` / `clause_labeled` / `clause_unlabeled` / `clause_distinct` 四个键（**加 `clause_` 前缀是为了不与 `anchor_blocks` 的同名 `blocks` 键相撞**）

- [ ] **Step 1: 写失败用例（正例 + 降级例）**

追加到 `tests/test_ocr_layout.py` 末尾：

```python
def test_finalize_layout_attaches_clause_no_to_blocks(tmp_path):
    """★ P2.5 接线：块必须带上所属条文号（spec §11.2）。

    此前 `align_clauses` 只有单测、**生产链路从不调用** ⇒ layout.json 里
    `clause_no` 字段一个都没有，审查页标题栏与「点条号跳转」双双失效。
    """
    import app.ocr.layout as mod
    mod.save_raw_layout_safely(_one_block_records("本条正文"), str(tmp_path))
    stats = mod.finalize_layout(str(tmp_path), "1 总则\n\n本条正文")

    assert stats is not None
    lay = mod.load_layout(tmp_path)
    assert lay is not None
    blk = lay["blocks"][0]
    assert "clause_no" in blk, "块未带 clause_no 字段 —— 接线没生效"
    assert blk["clause_no"] == "1", f"块归属条文号错误：{blk['clause_no']!r}"
    assert stats["clause_labeled"] == 1 and stats["clause_unlabeled"] == 0


def test_finalize_layout_degrades_when_alignment_raises(tmp_path, monkeypatch):
    """★ 对齐失败必须**降级**（块保留、条号为 None），不得丢掉整份 layout。

    条号是增强信息；若让它把 `finalize_layout` 整条拖挂，审查页会退化成
    「无版面数据」错误页 —— 那比没有条号严重得多。
    """
    import app.ocr.layout as mod

    def boom(*a, **k):
        raise ValueError("对齐炸了")

    monkeypatch.setattr(mod, "align_clauses", boom)
    mod.save_raw_layout_safely(_one_block_records("本条正文"), str(tmp_path))
    stats = mod.finalize_layout(str(tmp_path), "1 总则\n\n本条正文")

    assert stats is not None, "对齐失败不应让 finalize_layout 返回 None"
    lay = mod.load_layout(tmp_path)
    assert lay is not None and len(lay["blocks"]) == 1, "块数据被丢掉了"
    assert lay["blocks"][0]["clause_no"] is None
    assert stats["clause_labeled"] == 0
```

- [ ] **Step 2: 跑用例确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py -q -k clause_no`
Expected: FAIL —— 第一条 `AssertionError: 块未带 clause_no 字段`；第二条 `finalize_layout` 返回 `None`（异常被既有 except 吞掉）⇒ `assert stats is not None` 失败。

- [ ] **Step 3: 实现（新增降级包装 + 接线）**

在 `app/ocr/layout.py` 的 `align_clauses` **之后**（`return out, {...}` 那个 `return` 下面）追加：

```python
def _align_or_degrade(md_text: str, blocks: list[dict]) -> tuple[list[dict], dict]:
    """条号对齐的**降级包装**：任何异常都退回「全部无条号」，绝不向上抛。

    与 `finalize_layout` 里锚定失败的处理口径一致：**保住 layout 主体**，
    只损失增强字段。返回的 stats 键统一加 `clause_` 前缀，避免与
    `anchor_blocks` 的 `blocks` / `anchored` 等键在合并时相撞。
    """
    try:
        return align_clauses(md_text, blocks)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        logger.warning("条文号对齐失败（不影响锚定，块将不带条号）: %s", e)
        n = len(blocks)
        return ([{**b, "clause_no": None} for b in blocks],
                {"clause_blocks": n, "clause_labeled": 0,
                 "clause_unlabeled": n, "clause_distinct": 0})
```

> ⚠️ 本函数定义在 `align_clauses` **下面**、`finalize_layout` **上面** —— 三者的相对顺序无所谓（Python 调用时才解析名字），但把它放在 `align_clauses` 紧邻处便于阅读。

然后把 `finalize_layout` 体内这两行：

```python
        anchored, stats = anchor_blocks(base_text, raw.get("blocks") or [], ignore)
        layout = {
            "version": LAYOUT_VERSION,
            "pages": raw.get("pages") or [],
            "blocks": anchored,
```

改为：

```python
        anchored, stats = anchor_blocks(base_text, raw.get("blocks") or [], ignore)
        # spec §11.2：块 → 条文号。**必须在这里落库**——审查页只读 layout.json，
        # 不在请求期重算（那会让 /layout 端点每次请求都跑一遍 parse_markdown）。
        blocks, align_stats = _align_or_degrade(base_text, anchored)
        layout = {
            "version": LAYOUT_VERSION,
            "pages": raw.get("pages") or [],
            "blocks": blocks,
```

并把同一 dict 里的 `"stats":` 一行：

```python
            "stats": {k: v for k, v in stats.items() if k != "residual_miss"},
```

改为：

```python
            "stats": {**{k: v for k, v in stats.items() if k != "residual_miss"},
                      **align_stats},
```

- [ ] **Step 4: 跑用例确认通过 + 跑邻近增量**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_layout.py tests/test_layout_clauses.py tests/test_layout_anchor.py -q`
Expected: 全绿（`test_layout_clauses.py` 的 8 例不受影响 —— 它直接测 `align_clauses`，未走包装）

- [ ] **Step 5: 跑 pyright**

Run: `D:/Python/python.exe -m pyright app tests scripts`
Expected: 0 errors 0 warnings（与基线一致）

- [ ] **Step 6: 提交**

```bash
git add app/ocr/layout.py tests/test_ocr_layout.py
git commit -m "fix: finalize_layout 接线 align_clauses，layout.json 补条文号（失败降级）"
```

---

### Task 2: 前端 `:key` 与预览面板压扁

**Files:**
- Modify: `app/templates/partials/ocr_review_blocks.html:129`、`static/app.css:875`、`app/templates/base.html`
- Test: `tests/test_ocr_review.py`（末尾追加）

**Interfaces:**
- Consumes: 无
- Produces: 切分列表的 Alpine key 改为**行下标**，任何 `clause_no`（含空串、重复号）都能一行一行地画出来；`.rvb-preview` 不再被 flex 压扁

- [ ] **Step 1: 写失败用例（钉住模板意图）**

追加到 `tests/test_ocr_review.py` 末尾：

```python
def test_clause_list_keys_by_index_not_by_clause_no():
    """★ 切分列表的 `:key` 必须用**行下标**。

    用 `clause_no` 做 key 时，重复号（后端 `c.get("clause_no") or ""` 可产空串，
    真实数据里也曾出现同一个号三次）会让 Alpine 的 keyed diff 在列表**变更**时
    崩掉 —— 症状是「编辑并保存后切分面板只剩标题行，刷新页面又正常」。
    """
    from pathlib import Path
    src = Path("app/templates/partials/ocr_review_blocks.html").read_text(encoding="utf-8")
    assert 'x-for="(c, ci) in preview.clauses"' in src, \
        f"切分列表模板结构变了，本用例需同步：{[l for l in src.splitlines() if 'x-for' in l and 'preview' in l]}"
    assert 'clause_no' not in src.split('x-for="(c, ci) in preview.clauses"')[1].split(">")[0], \
        "切分列表的 x-for 仍按 clause_no 取值 —— key 必须改用下标"


def test_preview_panel_is_not_flex_shrinkable():
    """★ 切分面板不得可收缩：子列表 `overflow:auto` 的最小高度为 0，
    空间不足时面板会被压到只剩 header（同样伪装成「只剩标题行」）。

    ⚠️ Task 6 把该规则由 `.rvb-preview` 改名为 `.rvb-cut` —— 届时**同步改本断言**，
    不是删掉它。
    """
    from pathlib import Path
    css = Path("static/app.css").read_text(encoding="utf-8")
    line = [l for l in css.splitlines() if l.strip().startswith(".rvb-preview {")]
    assert line, ".rvb-preview 规则不见了"
    assert "flex: 0 0 auto" in line[0], f"未固定高度：{line[0]}"
```

- [ ] **Step 2: 跑用例确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_review.py -q -k "clause_list_keys or not_flex_shrinkable"`
Expected: 两条都 FAIL。

- [ ] **Step 3: 实现**

`app/templates/partials/ocr_review_blocks.html:129`，把：

```html
                    <template x-for="c in preview.clauses" :key="'pc' + c.clause_no">
```

改为：

```html
                    <!-- key 用**下标**：clause_no 可能重复（后端会产出空串），
                         用它做 key 会让 Alpine 在列表变更时崩成「只剩标题行」 -->
                    <template x-for="(c, ci) in preview.clauses" :key="'pc' + ci">
```

`static/app.css:875`，把：

```css
.rvb-preview { border-top: 1px solid var(--pico-muted-border-color); max-height: 220px; display: flex; flex-direction: column; }
```

改为：

```css
/* flex: 0 0 auto —— 见 tests/test_ocr_review.py::test_preview_panel_is_not_flex_shrinkable */
.rvb-preview { border-top: 1px solid var(--pico-muted-border-color); max-height: 220px; flex: 0 0 auto; display: flex; flex-direction: column; }
```

- [ ] **Step 4: 递增静态资源版本**

`app/templates/base.html` 中 `app.css?v=43` → `?v=44`。

- [ ] **Step 5: 跑用例确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_review.py -q`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add app/templates/partials/ocr_review_blocks.html static/app.css app/templates/base.html tests/test_ocr_review.py
git commit -m "fix: 切分列表 key 改用下标、预览面板不可收缩（修「只剩标题行」）"
```

---

### Task 3: 页图按 devicePixelRatio 请求（T39）

**Files:**
- Modify: `static/components/review-blocks.js:86-97`、`app/routes/import_routes.py:1276`、`app/templates/base.html`
- Test: `tests/test_review_layout_routes.py`（末尾追加）、`scripts/probe_spec_ui.py`（f13 新用例）
- Test: `tests/test_ocr_review.py`（追加 JS 源码断言）

**Interfaces:**
- Consumes: `GET /import/review/{task_id}/page/{n}.png?scale=<float>`（既有）
- Produces: 请求倍率变为 `CSS 倍率 × devicePixelRatio`；端点 `scale` 上限 3.0 → **8.0**

- [ ] **Step 1: 写失败用例（端点上限 + JS 请求式）**

追加到 `tests/test_review_layout_routes.py` 末尾：

```python
def test_page_image_accepts_hidpi_scale(auth_client):
    """★ dpr=2 的屏幕上，适应宽度后请求倍率会到 ~3.0×2=6.0，
    旧上限 3.0 会 422 ⇒ 页图整个加载不出来（比模糊更糟）。"""
    r = auth_client.get("/import/review/deadbeef/page/1.png?scale=6.0")
    assert r.status_code != 422, f"6.0 被判非法：{r.status_code} {r.text[:120]}"
    r2 = auth_client.get("/import/review/deadbeef/page/1.png?scale=9.0")
    assert r2.status_code == 422, "上限没兜住 —— 渲染倍数必须有护栏"
```

追加到 `tests/test_ocr_review.py` 末尾：

```python
def test_page_url_multiplies_scale_by_device_pixel_ratio():
    """★ 1 图像像素 : 1 CSS 像素是模糊的根因（200% 缩放下被 2 倍上采样）。
    请求倍率必须乘上 devicePixelRatio。"""
    from pathlib import Path
    src = Path("static/components/review-blocks.js").read_text(encoding="utf-8")
    seg = src.split("pageUrl(n)")[1].split("}")[0]
    assert "devicePixelRatio" in seg, f"pageUrl 未乘 dpr：{seg!r}"
```

- [ ] **Step 2: 跑用例确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_review_layout_routes.py tests/test_ocr_review.py -q -k "hidpi or device_pixel"`
Expected: 两条都 FAIL（前者 6.0 得 422；后者找不到 `devicePixelRatio`）。

- [ ] **Step 3: 实现**

`static/components/review-blocks.js:86-89`，把：

```js
        pageUrl(n) {
            const s = this.scaleMode === 'fit' ? this._fitScale() : this.scaleMode;
            return `/import/review/${this.taskId}/page/${n}.png?scale=${Number(s).toFixed(2)}`;
        },
```

改为：

```js
        /** 请求倍率 = CSS 倍率 × devicePixelRatio。
         *
         * ⚠️ 不乘 dpr 时：PNG 像素宽 ≡ 容器 CSS 宽（与单位无关地恒等）⇒ 恒为
         * 「1 图像像素 : 1 CSS 像素」。在 200% 显示缩放的屏幕上，浏览器要 2 个
         * 设备像素而位图只有 1 个 ⇒ 放大插值 ⇒ 糊。**画布 CSS 宽不用改**：
         * `.rvb-img{width:100%}` 会把多出来的像素正确地缩回去。
         */
        pageUrl(n) {
            const css = this.scaleMode === 'fit' ? this._fitScale() : this.scaleMode;
            const s = css * (window.devicePixelRatio || 1);
            return `/import/review/${this.taskId}/page/${n}.png?scale=${Number(s).toFixed(2)}`;
        },
```

`app/routes/import_routes.py:1276`，把：

```python
    if not 0.25 <= scale <= 3.0:
        return JSONResponse({"detail": "scale 需在 [0.25, 3.0]"}, status_code=422)
```

改为（**上限 8.0 = `_fitScale` 的 3.0 上限 × 最高支持的 dpr 2.5，再留一档余量**）：

```python
    if not 0.25 <= scale <= 8.0:
        return JSONResponse({"detail": "scale 需在 [0.25, 8.0]"}, status_code=422)
```

- [ ] **Step 4: 递增静态资源版本**

`app/templates/base.html` 中 `review-blocks.js?v=7` → `?v=8`。

- [ ] **Step 5: 跑用例确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_review_layout_routes.py tests/test_ocr_review.py -q`
Expected: 全绿。

- [ ] **Step 6: 加探针用例（端到端证明像素密度真的对了）**

在 `scripts/probe_spec_ui.py` 中新增（放在 `f13_18` 之后）：

```python
def f13_19_page_image_is_rendered_at_device_pixel_ratio(page):
    """★★ T39：页图的**位图分辨率**必须跟上屏幕 dpr —— 不是看它「清不清楚」，
    而是直接量 `naturalWidth / clientWidth ≈ devicePixelRatio`。"""
    task_id = _rvb_seed(page, "19")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-img").count() > 0, 10000), "页图未出现"
    ratio = page.evaluate(
        "() => { const i = document.querySelector('.rvb-img');"
        " return i.naturalWidth / i.clientWidth; }")
    dpr = page.evaluate("() => window.devicePixelRatio || 1")
    assert abs(ratio - dpr) < 0.15, \
        f"页图位图密度 {ratio:.2f} 与屏幕 dpr {dpr:.2f} 不符 —— 仍在 1:1 栅格化"
```

并把它加进 `CASES["f13"]` 列表末尾。

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: `19/19`（若 19 之前有用例红，按 T37 读作「19 未被本次运行覆盖」）
Expected（变异证据）: 把 `pageUrl` 里的 `* (window.devicePixelRatio || 1)` 摘掉 ⇒ `f13_19` 红。

- [ ] **Step 7: 提交**

```bash
git add static/components/review-blocks.js app/routes/import_routes.py app/templates/base.html scripts/probe_spec_ui.py tests/test_review_layout_routes.py tests/test_ocr_review.py
git commit -m "fix: 页图按 devicePixelRatio 请求，端点 scale 上限抬到 8.0（T39）"
```

---

## 阶段二：修 parser 的编号误判（承重判据）

### Task 4: 字母编号模式收紧为「点号必填」

**Files:**
- Modify: `app/parser/md_parser.py:26`
- Test: `tests/test_md_parser.py`（末尾追加）

**Interfaces:**
- Consumes: `_NUM_PATTERNS[1]`（被 `_match_clause_line` 与 `_extract_clause_no` **共用** —— 改一处即两处生效）
- Produces: 正文行「`GB 5768 的本部分…`」不再被当作条号；合法字母编号（`A.1` / `D.4.1` / `B. 0.1` / `M.O.2` / `附录 A`）**全部保持原样**

- [ ] **Step 1: 写失败用例（三条：副作用、副作用、防误伤）**

追加到 `tests/test_md_parser.py` 末尾：

```python
# ═══════════════════════════════════════════
# 标准号/材料牌号行被误判成条号（2026-10-05 用户实测 GB 5768.1-2009 引出）
# ═══════════════════════════════════════════
# 成因：`_NUM_PATTERNS` 的字母模式把点号写成**可选**，于是「GB 5768 的…」
# 被吃成 `GB` + `5768` 的附录式编号。后果有两层：
#   ① 凭空产出伪条（生产库已有 8 条：GB6095 / JGJ46 / HPB235 / JTG/T3610-2019 …）；
#   ② 它**抢占所属条的正文** ⇒ 该条被判「无自身正文」= 内节点 ⇒ **整条丢弃**
#      （实测：「## 1 范围」的正文首行是「GB 5768 的本部分规定了…」⇒ 「1」消失）。

def test_standard_designation_line_is_not_a_clause():
    """★ 正文行「GB 5768 的…」不得成为条号；其所属条「1」必须存活"""
    md = ("## 1 范围\n\n"
          "GB 5768 的本部分规定了道路交通标志和标线的原则和一般规定。\n\n"
          "## 2 规范性引用文件\n\n"
          "下列文件中的条款通过引用而成为本部分的条款。\n")
    rows = parse_markdown(md)
    nums = [r["clause_no"] for r in rows]
    assert not any(n.replace(" ", "").startswith("GB") for n in nums), \
        f"标准号行被误判成条号：{nums}"
    assert "1" in nums, f"「1 范围」被伪条吞掉了（现有：{nums}）"
    assert "2" in nums


def test_material_grade_line_is_not_a_clause():
    """★ 材料牌号行（HPB235 / JGJ46 形态）同样不得成条"""
    md = ("## 4.2 使用\n\n"
          "4.2.1 钢筋应符合下列规定。\n\n"
          "HPB235 钢筋的强度标准值应按表 4.2.1 采用。\n")
    rows = parse_markdown(md)
    nums = [r["clause_no"] for r in rows]
    assert "HPB235" not in nums, f"牌号被误判成条号：{nums}"
    assert "4.2.1" in nums


def test_dotted_letter_clause_numbers_still_work():
    """★ 防误伤：收紧后合法字母编号必须**逐条**仍然成立"""
    md = ("附录A 接头型式检验的加载制度\n\n"
          "A.1 检验设备\n\n"
          "### 附录 B 压实度评定\n\n"
          "B. 0.1 路基和路面基层的压实度应以重型击实标准为准。\n\n"
          "D.4.1 开挖深度 20 m 及以上的岸坡开挖工程。\n")
    nums = [r["clause_no"] for r in parse_markdown(md)]
    for want in ("附录A", "A.1", "附录B", "B.0.1", "D.4.1"):
        assert want in nums, f"合法编号 {want} 被误伤（现有：{nums}）"
```

- [ ] **Step 2: 跑用例确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py -q -k "standard_designation or material_grade or dotted_letter"`
Expected: 前两条 FAIL（`GB5768` / `HPB235` 混进 `nums`；第一条还会有 `'1' not in nums`）；第三条应已 PASS（防误伤的基线）。

- [ ] **Step 3: 实现（点号从可选改必填）**

`app/parser/md_parser.py:25-26`，把：

```python
    # 字母+数字编号: D.4, D.4.1, A.1, B. 0.1 (字母后跟数字，可选点分隔)
    r'^([A-Z]+(?:\s*[.．]?\s*' + _NUM_SEG + r')+)\s+(.+)',
```

改为：

```python
    # 字母+数字编号: D.4, D.4.1, A.1, B. 0.1（点号**必填**）
    #
    # ⚠️ 点号曾是可选的（`[.．]?`），这会让**正文行**「GB 5768 的本部分…」「HPB235 钢筋…」
    #    被吃成条号 —— 标准号/材料牌号在中文规范正文里遍地都是。伪条还会抢占所属条的
    #    正文，令那条被判内节点而**整条消失**（实测「## 1 范围」就这么没的）。
    #    实测口径：生产库全部合法字母条号（A.1.1 / B.0.1 / C.0.1 / …）**都带点号**，
    #    120 个 parser 用例也无一依赖无点号形态。
    r'^([A-Z]+(?:\s*[.．]\s*' + _NUM_SEG + r')+)\s+(.+)',
```

- [ ] **Step 4: 跑门禁（memory §五 的四道，一道都不能省）**

Run: `D:/Python/python.exe -m pytest tests/test_md_parser.py tests/test_parse_conservation.py -q`
Expected: **126 passed**（已预跑验证）

Run: `D:/Python/python.exe scripts/survey_structure.py`
Expected: 12 项指标与改动前**逐项对比**，降级行计数（指标 12）不得上升

Run: 两份真语料 —— `tests/fixtures/cjj2_source.md`（**sha256 勿改**）与 `data/outputs/aa96b73a/aa96b73a.md`（缺则跳过）解析后条文数与改动前对比，只允许「伪条消失」方向的差异

- [ ] **Step 5: 跑 pyright**

Run: `D:/Python/python.exe -m pyright app tests scripts`
Expected: 0 errors 0 warnings

- [ ] **Step 6: 提交**

```bash
git add app/parser/md_parser.py tests/test_md_parser.py
git commit -m "fix: 字母编号模式点号必填，标准号/牌号正文行不再被误判成条号"
```

---

### Task 5: 重导受伪条污染的已入库规范

**Files:**
- 无代码改动（使用既有 `scripts/reimport_specs.py`）

**Interfaces:**
- Consumes: Task 4 修复后的 `parse_markdown`
- Produces: 库中 8 条伪条及其「被吞掉的条文」被修正

> ⚠️ **本 Task 是破坏性操作**，按项目 `CLAUDE.md` §1.5 必须先向用户确认再执行。`--apply` 会删除目标规范的**全部条文与向量**。

- [ ] **Step 1: 列出受影响的规范 id（只读）**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -c "
import sqlite3
c = sqlite3.connect('data/spec_query.db')
q = '''SELECT DISTINCT spec_id FROM clauses
       WHERE clause_no GLOB '[A-Z]*' AND clause_no NOT LIKE '%.%'
         AND clause_no NOT LIKE '附录%' '''
ids = [r[0] for r in c.execute(q)]
for i in ids:
    n = c.execute('SELECT count(*) FROM clauses WHERE spec_id=?', (i,)).fetchone()[0]
    print(i, '条文数', n)
c.close()"
```

Expected: 4 个 spec_id（本次实测口径：共 4 本规范受影响）。

- [ ] **Step 2: 备份（不做备份不得进下一步）**

```bash
cp data/spec_query.db "data/spec_query.db.bak-$(date +%Y%m%d-%H%M%S)"
cp -r lance_db "lance_db.bak-$(date +%Y%m%d-%H%M%S)"
```

- [ ] **Step 3: dry-run 并请用户确认**

```bash
D:/Python/python.exe scripts/reimport_specs.py <id1> <id2> <id3> <id4>
```

把 dry-run 输出（将删什么、将重建什么）**原文呈现给用户**，得到明确同意后才继续。

⚠️ 重导期间不能有在跑的导入任务（重导会与导入抢同一批表）。执行前查一次「在途/待审查任务」，非 0 则先等或先清理。

- [ ] **Step 4: 执行**

```bash
D:/Python/python.exe scripts/reimport_specs.py <id1> <id2> <id3> <id4> --apply
```

- [ ] **Step 5: 校验（数字必须现场数，不许推算）**

```bash
export PYTHONUTF8=1
D:/Python/python.exe -c "
import sqlite3
c = sqlite3.connect('data/spec_query.db')
q = '''SELECT count(*) FROM clauses
       WHERE clause_no GLOB '[A-Z]*' AND clause_no NOT LIKE '%.%'
         AND clause_no NOT LIKE '附录%' '''
print('残留伪条:', c.execute(q).fetchone()[0])
c.close()"
```

Expected: `残留伪条: 0`

- [ ] **Step 6: 记录**

把重导前后的条文数写进 `TODOS.md` 的 T38 条目（**库级指标**，与夹具级区分）。

---

## 阶段三：三栏布局

### Task 6: 切分预览从「右栏下部」改为独立第三栏

**Files:**
- Modify: `app/templates/partials/ocr_review_blocks.html:49-139`、`static/app.css:836-838, 875`、`app/templates/base.html`
- Test: `scripts/probe_spec_ui.py`（f13 新用例）

**Interfaces:**
- Produces: DOM 变为 `.rvb-panes > [.rvb-left, .rvb-right, .rvb-cut]` 三个**兄弟**节点；切分面板不再嵌在 `.rvb-right` 里

- [ ] **Step 1: 加探针用例（先红后绿）**

```python
def f13_20_panes_are_three_equal_height_columns(page):
    """★ 三栏：左 PDF / 中 版面编辑 / 右 切分，**等高**且顺序固定。

    此前切分面板嵌在右栏下部（`.rvb-cut` 是 `.rvb-right` 的**子**节点），
    220px 的横条把编辑区挤扁，左中对照被割裂。
    """
    task_id = _rvb_seed(page, "20")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-cut").count() > 0, 10000), "第三栏未出现"
    order = page.evaluate(
        "() => [...document.querySelector('.rvb-panes').children]"
        ".map(e => e.className.split(' ')[0])")
    assert order == ["rvb-left", "rvb-right", "rvb-cut"], f"栏序不对：{order}"
    hs = page.evaluate(
        "() => [...document.querySelector('.rvb-panes').children]"
        ".map(e => Math.round(e.getBoundingClientRect().height))")
    assert max(hs) - min(hs) <= 2, f"三栏不等高：{hs}"
    tops = page.evaluate(
        "() => [...document.querySelector('.rvb-panes').children]"
        ".map(e => Math.round(e.getBoundingClientRect().top))")
    assert max(tops) - min(tops) <= 2, f"三栏不顶对齐：{tops}"
```

- [ ] **Step 2: 实现 —— 模板**

`app/templates/partials/ocr_review_blocks.html`：把 `:117-137` 的整块 `<section class="rvb-preview"> … </section>` **剪切**出来，作为 `.rvb-panes` 的**第三个兄弟**，放在 `:138` 的 `</section>`（关 `.rvb-right`）**之后**、`:139` 的 `</div>`（关 `.rvb-panes`）之前。

即 `:115` 之后紧接 `</section>`（关 `.rvb-right`），然后完整插入下面这一块，最后才是 `</div>`（关 `.rvb-panes`）：

```html
        </section>

        <!-- 切分预览（spec §11.4）：**独立第三栏**。此前嵌在右栏下部（220px 横条），
             把编辑区挤扁、割裂左中对照；改为与左右栏等高的独立栏。
             header 里的计数与新增/消失标记、列表的 :key 均保持不变。 -->
        <section class="rvb-cut" x-show="!error" x-cloak>
            <header class="rvb-cut-hd">
                <strong>切分预览</strong>
                <span x-text="'当前 ' + preview.count + ' 条'"></span>
                <span class="rvb-added" x-show="preview.added.length"
                      x-text="'新增 ' + preview.added.length + ' 条'"></span>
                <span class="rvb-removed" x-show="preview.removed.length"
                      x-text="'消失 ' + preview.removed.length + ' 条'"></span>
                <span x-show="previewBusy" style="color:var(--pico-muted-color)">计算中…</span>
                <span x-text="previewErr" style="color:#c00"></span>
            </header>
            <ol class="rvb-clause-list">
                <!-- key 用**下标**：clause_no 可能重复（后端会产出空串），
                     用它做 key 会让 Alpine 在列表变更时崩成「只剩标题行」（Task 2） -->
                <template x-for="(c, ci) in preview.clauses" :key="'pc' + ci">
                    <li :class="preview.added.includes(c.clause_no) && 'rvb-clause-new'"
                        @click="openClause(c.clause_no)">
                        <span class="rvb-cn" x-text="c.clause_no"></span>
                        <span x-text="c.title"></span>
                    </li>
                </template>
            </ol>
        </section>
```

> ⚠️ **不要把 Task 7 的 `isClauseActive` 提前写进来** —— 那个方法此刻还不存在，Alpine 求值时会抛错，整个组件挂掉。本 Task 保持 `:class` 的既有单值写法，Task 7 再改成数组写法。

- [ ] **Step 3: 实现 —— 样式**

`static/app.css:837-838`，把：

```css
.rvb-panes { display: flex; gap: 12px; flex: 1; min-height: 0; }
.rvb-left, .rvb-right { flex: 1 1 50%; min-width: 0; display: flex; flex-direction: column; }
```

改为：

```css
.rvb-panes { display: flex; gap: 12px; flex: 1; min-height: 0; }
/* 左 PDF 与中 编辑区各占一半，右栏切分预览**固定宽** ——
   它的内容只有「条号 + 短标题」，宽屏上不需要跟着变宽。
   用户裁定（2026-10-05）：三栏等高、顺序 左/中/右。 */
.rvb-left, .rvb-right { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; }
.rvb-cut { flex: 0 0 280px; min-width: 0; display: flex; flex-direction: column;
           border: 1px solid var(--pico-muted-border-color); border-radius: 6px; }
```

并把 `:875` 的 `.rvb-preview` 规则**删除**（其视觉职责由 `.rvb-cut` 承担），`.rvb-preview-hd` 改名为 `.rvb-cut-hd`、去掉 `border-top` 相关描述：

```css
.rvb-cut-hd { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center;
              padding: 0.4rem 0.6rem; font-size: 0.85rem; }
```

**同一提交里必须同步改两处选择器**，否则改名会打断既有用例：

1. `tests/test_ocr_review.py::test_preview_panel_is_not_flex_shrinkable`（Task 2 建的）—— 它找的是 `.rvb-preview {`：

```python
    line = [l for l in css.splitlines() if l.strip().startswith(".rvb-cut {")]
    assert line, ".rvb-cut 规则不见了"
```

2. `scripts/probe_spec_ui.py:2082`（**f13_14**）—— `page.locator(".rvb-preview-hd")` 改成 `.rvb-cut-hd`：

```python
    assert "消失" in page.locator(".rvb-cut-hd").inner_text(), "没有给出「消失 N 条」的提示"
```

（已实测：全仓 `.rvb-preview` / `.rvb-preview-hd` 的引用**只有** `ocr_review_blocks.html:117-118`、`app.css:875-876`、`probe_spec_ui.py:2082` 三处，改完即无残留。）

- [ ] **Step 4: 递增静态资源版本** —— `app.css?v=44` → `?v=45`

- [ ] **Step 5: 跑探针**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: `20/20`
Expected（变异证据）: 把 `.rvb-cut` 的 `flex: 0 0 280px` 改回 `flex: 1 1 50%` ⇒ 栏序断言仍绿、但换掉 `max-height` 后高度断言应能区分 —— **若两条都绿，说明本用例没钉住东西，必须加严**（本仓 T37 的教训）。

- [ ] **Step 6: 提交**

```bash
git add app/templates/partials/ocr_review_blocks.html static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 切分预览改为独立第三栏（左 PDF / 中 编辑 / 右 切分，等高）"
```

---

### Task 7: 三栏联动 —— 点条号跳转、当前块条号高亮、栏可折叠

**Files:**
- Modify: `static/components/review-blocks.js`、`app/templates/partials/ocr_review_blocks.html`、`static/app.css`、`app/templates/base.html`
- Test: `scripts/probe_spec_ui.py`（f13 新用例）

**Interfaces:**
- Consumes: Task 1 落库的 `b.clause_no`；既有 `focusBlock(i)`；既有 `activeIdx`
- Produces: `clausesOfBlock(i) -> string[]`（该块所属条号，供高亮用）；`cutOpen: boolean`（第三栏折叠态）

- [ ] **Step 1: 加探针用例**

```python
def f13_21_clicking_a_clause_scrolls_to_its_block(page):
    """★ 点切分栏的条号 ⇒ 滚动到并高亮对应块（此前 `openClause` 恒 findIndex=-1，
    因为块上根本没有 clause_no —— Task 1 接线后才可能成立）。"""
    task_id = _rvb_seed(page, "21")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000)
    first = page.locator(".rvb-clause-list li").first
    no = first.locator(".rvb-cn").inner_text().strip()
    first.click()
    assert poll_until(page, lambda: page.locator(".rvb-block--on").count() == 1, 3000), \
        f"点条号 {no} 后没有块被高亮"
    on = page.locator(".rvb-block--on .rvb-clause").first.inner_text().strip()
    assert on == no, f"高亮的块条号 {on!r} 与被点的 {no!r} 不符"


def f13_22_focused_block_highlights_its_clause_in_the_cut_column(page):
    """★ 反向联动：聚焦块的条号在切分栏里高亮"""
    task_id = _rvb_seed(page, "22")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-block .rvb-clause").count() > 0, 10000)
    page.locator(".rvb-block").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li.rvb-clause-on").count() == 1, 3000), \
        "聚焦块后切分栏没有对应条号高亮"


def f13_23_cut_column_collapses_and_gives_width_back(page):
    """★ 窄屏出口：第三栏可折叠，折叠后左右两栏分掉腾出来的宽度"""
    task_id = _rvb_seed(page, "23")
    assert task_id is not None, "前置不成立"
    left_w = lambda: page.evaluate(
        "() => Math.round(document.querySelector('.rvb-right').getBoundingClientRect().width)")
    before = left_w()
    page.locator(".rvb-cut-hd button:has-text('收起')").click()
    # 用 is_visible 而非 count：x-show 折叠后节点仍在 DOM 里，count 恒为 1（会假绿）
    assert poll_until(page, lambda: not page.locator(".rvb-cut .rvb-clause-list").is_visible(), 3000), \
        "未折叠"
    assert left_w() > before, "折叠后中栏没有变宽"
```

- [ ] **Step 2: 实现 —— JS**

在 `review-blocks.js` 的 state 里加 `cutOpen: true,`；把 `openClause` 补上防御与高亮：

```js
        /** 点切分栏的条号 ⇒ 滚到并高亮它的块（依赖 Task 1 落库的 b.clause_no）。 */
        openClause(no) {
            const i = this.blocks.findIndex((b) => b.clause_no === no);
            if (i < 0) return;              // 新增的条号可能没有对应块（见 T38 的归属口径）
            this.focusBlock(i);
        },

        /** 某块所属的条号集合（供切分栏高亮；空块返回空数组） */
        clausesOfBlock(i) {
            const b = this.blocks[i];
            return (b && b.clause_no) ? [b.clause_no] : [];
        },

        /** 切分栏里某条是否应当高亮（= 属于当前聚焦块） */
        isClauseActive(no) {
            return this.activeIdx >= 0 && this.clausesOfBlock(this.activeIdx).includes(no);
        },
```

模板里切分列表的 `<li>`（Task 6 落地的那段）—— 把**单值** `:class` 改成**数组**写法，让「新增」与「高亮」能同时成立：

```html
                    <li :class="[preview.added.includes(c.clause_no) && 'rvb-clause-new',
                                  isClauseActive(c.clause_no) && 'rvb-clause-on']"
                        @click="openClause(c.clause_no)">
```


`rvb-cut-hd` 里加折叠按钮：

```html
                <button type="button" class="outline" style="margin:0"
                        @click="cutOpen = !cutOpen"
                        x-text="cutOpen ? '收起' : '展开'"></button>
```

`.rvb-clause-list` 加 `x-show="cutOpen"`；`.rvb-cut` 加 `:style="cutOpen ? '' : 'flex:0 0 44px'"`。

- [ ] **Step 3: 实现 —— 样式**

```css
.rvb-clause-on { background: #fff3e0; font-weight: 600; }
```

（`#fff3e0` 与既有高亮色 `#e8710a` 同族，避免与「新增绿底」`#e8f5e9` 撞色。）

- [ ] **Step 4: 递增静态资源版本** —— `review-blocks.js?v=8` → `?v=9`、`app.css?v=45` → `?v=46`

- [ ] **Step 5: 跑探针**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: `23/23`

- [ ] **Step 6: 跑 pyright 与增量测试**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_review.py tests/test_layout_clauses.py tests/test_ocr_layout.py -q`
Run: `D:/Python/python.exe -m pyright app tests scripts`
Expected: 全绿 / 0 errors

- [ ] **Step 7: 提交**

```bash
git add static/components/review-blocks.js app/templates/partials/ocr_review_blocks.html static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 三栏联动（点条号跳转、聚焦块高亮其条号、切分栏可折叠）"
```

---

## 收尾（不属于任何 Task，但必须做）

- [ ] 更新 `TODOS.md`：**关闭 T38 的形态部分与 T39**（写明改后形态与 dpr 口径），保留「`当前 N 条` 计数位置」等未定项。
- [ ] 全量回归：`D:/Python/python.exe -m pytest tests/ -q`（预期基线 1452 passed + 本计划新增用例）。
- [ ] 探针全组：`f13 / f12 / f10 / f11` 各自跑一遍（每轮前重新 `cp` 隔离库）。
- [ ] 提示用户 **Ctrl+F5 强刷**（`app.css` 到 v46、`review-blocks.js` 到 v9）。
- [ ] 推送：本次会话已有 3 个提交未推送（`a780747` / `0fab4d0` / `e98c81d`），用户裁定**与 T38/T39 一起推**。
