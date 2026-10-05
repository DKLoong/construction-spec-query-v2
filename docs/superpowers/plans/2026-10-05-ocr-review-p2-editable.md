# OCR 审查页改版 · P2 初次可编辑 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让审查者在右侧版面块上**按行改**（行内粗体/斜体/删除线 + 整行左中右对齐），改完**立刻看到这份 md 会被切成哪些条**，提交仍走既有 raw body 通道，导入结果与「手改 md」等价。

**Architecture:** 编辑**不落库** —— 「保存」只写前端块状态（spec §8.1）。提交时把 `{块下标: 新内容}` 发给新的 `compose` 端点，后端用 **P0 已交付并被 f12 反复验证的 `splice`** 只替换被编辑的行区间（未编辑区间原样取回原行 ⇒ 保真是结构上恒真），再套一层**存储期规范化**（表格统一居中 + 字面 `\n` → `<br>`），把结果当作 md 交给既有 `confirm`。实时切分预览复用**同一条 compose 管线**再跑 `parse_markdown` —— 预览与实际导入**同源**，不是第二把尺子。行内富文本用**逐行 contenteditable + 自写的 md↔DOM 转换器**（纯函数，探针可逐条钉）。

**Tech Stack:** FastAPI + Jinja2 + Alpine.js + 原生 contenteditable + Playwright 探针

**Spec:** `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`（§8.1 / §8.2 / §8.6 / §10 / §11.3 / §11.4 / §13 / §15 / §18·P2）

**前置（已交付）**：P0 的 `app/ocr/layout.py`（`anchor_blocks` / `splice` / `align_clauses` / `load_layout`）与 P1 的版面块审查页（`partials/ocr_review_blocks.html` + `static/components/review-blocks.js`，f12 7/7）。

---

## Global Constraints

1. **`md_text` 对外契约不变** —— `parse_markdown` / `plain_text` / `build_embed_text` 零改动；**不得改 `md_parser.py` 的判定逻辑**（只允许**读**它的诊断钩子）。
2. **不新增依赖**（无 JS 构建、无 npm 包；前端只用仓内已 vendored 的 marked/DOMPurify/KaTeX）。
3. **不碰真实 `data/`**：测试与探针一律隔离四路径（`DATABASE_PATH`/`UPLOAD_DIR`/`OUTPUT_DIR`/`LANCE_DB_PATH`）+ 独立端口 **8123**；跑完清理。
4. **编号行的行首永不改动**：行首是编号（或 `#` 标题）的行**不得被任何包裹/对齐改写**。依据（spec §8.6 实测再确认）：标签与内容同行会让**全篇切出 0 条**。
5. **删除线必须成对输出** `~~…~~`，**不得输出单个 `~`**（本仓 2026-10-02 刚修过单波浪线被 marked 当删除线）。
6. **未定位块**（`start_line is None`）与**非文本类块**（表格/公式/图片/图表）：**照常渲染 + 明确标注 + 不可编辑**，**不得静默隐藏**。
7. 每个 Task 跑**增量测试** + `pyright <显式路径>`（**不得写 `python -m pyright`**，实测报 `No module named pyright`），再提交。
8. 逐个 `git add <文件>`，**不得 `git add .`**。
9. 改 `static/**` 或模板后，`base.html` 里 `?v=N` **必须递增**（否则浏览器/探针读旧 js）。
10. **提交信息里不得出现反引号**（bash 会当命令替换执行，实测吃掉过类名）；长信息写文件 + `git commit -F`。

---

## 本次规划前的实测（2026-10-05，实施时**直接引用本节数字，不得在别处另写**）

| # | 事实 | 出处 |
| --- | --- | --- |
| M1 | 参考夹具 `data/_ocr_reference/{raw.jsonl,reference.md}`：**650 块 / 57 页 / 30 975 字符 / 1 244 行**；可锚定 563、已锚定 **561**、未锚定 **2**（`table` idx 286 / `chart` idx 522） | 本机跑 `extract_layout` + `anchor_blocks` |
| M2 | label 分布：`text` 477 · `number` 57 · `paragraph_title` 47 · `display_formula` 21 · `formula_number` 21 · `image` 9 · `figure_title` 9 · `content` 4 · `vision_footnote` 3 · `table` 1 · `chart` 1 | 同上 |
| M3 | **可编辑文本类块 = 540**（`text`477 + `paragraph_title`47 + `figure_title`9 + `content`4 + `vision_footnote`3），**全部有 span**；块内行数最大 25（`content`）、`text` 最大 18 | 同上 |
| M4 | **最重真实产物 617 892 字符**（`8376e02c`）→ `parse_markdown` **109 ms**；46 万字符 **101 ms**；30 975 字符 **14 ms** | `min` of 3 次 |
| M5 | `splice`（含 130 处编辑）**0.4 ms** | 同上 |
| M6 | **参考夹具里字面 `\n` 出现 0 次**；但**真实产物 15 份里 14 份含**（合计 827 处，最多 `1516a11e` 125 处） | 全文计数 |
| M7 | **现有产物 0 份带 `layout.json`**（layout 只在 PaddleOCR-VL + 扫描件路径产生，存量产物均早于 P0）⇒ compose 无法在存量产物上演示 | `data/outputs/*/layout.json` 计数 |
| M8 | 夹具里 `<table` 出现 2 处 | 正则计数 |
| M9 | **对齐包裹的渲染实测**：`<div …>内容</div>`（同行或独占行）会被 marked 当 HTML 块 ⇒ **行内 markdown 原样露出**（`**加粗**` 不变粗）；`<span style="display:block;text-align:center">内容</span>` **同行**写法 ⇒ 输出 `<p><span style="…"><strong>加粗</strong>…</span></p>`，**强调保留、不新增行** | node 直接喂 `static/marked.min.js` |
| M10 | **行内标记渲染实测**：`**a*****b***` → `<strong>a</strong><em><strong>b</strong></em>`（相邻不同标记可直接拼接）；孤立 `*`（`3.0.1 采用 * 号标注`）**原样保留** | 同上 |

> M9 是**对 spec §8.6 的纠正**：spec 给的「标签独占行」写法（③/④）会让行内强调失效且**新增行**；改用 `<span …>` **同行**包裹后两个问题都不存在。本计划按 M9 实现，并在 Task 8 用探针把「不新增行 + 强调仍渲染」钉住。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/parser/ocr_clean.py`（改） | 追加 **`normalize_md(md_text)`** —— 存储期规范化（表格居中 + 字面 `\n`→`<br>`），纯函数、幂等 |
| `app/ocr/layout.py`（改） | 追加常量 **`EDITABLE_TEXT_LABELS`**（可编辑 label 白名单，前后端**单一来源**） |
| `app/routes/import_routes.py`（改） | 追加 `POST …/compose`、`POST …/preview`（共用前置 `_compose_or_error`）；`/layout` 响应加 `editable_labels`；`confirm_review` 接一行规范化 |
| `app/templates/partials/ocr_review_blocks.html`（改） | 右栏加「纠正」入口 / 行编辑器 / 切分预览面板 / 提交与取消 |
| `static/components/md-inline.js`（新建） | **纯函数**：md 行 ↔ 行内 HTML（成对标记、对齐包裹、逐字节可逆）；无副作用、可被探针直接调用 |
| `static/components/review-blocks.js`（改） | 编辑态（进入/取消/保存/行增删/工具条）、切分预览、提交链路 |
| `app/templates/partials/ocr_review.html`（改） | 源码审查页加一行提示：提交时会做规范化（用户 2026-10-05 裁定两条入口都生效） |
| `static/app.css`（改） | 新控件的样式 |
| `app/templates/base.html`（改） | 引入 `md-inline.js?v=1`，并递增 `review-blocks.js` / `app.css` 的版本号 |
| `tests/test_ocr_normalize.py`（新建） | `normalize_md` 单测 + 真实夹具的「条文序列不变」判据 |
| `tests/conftest.py`（改） | `isolated_paths` 补 `init_db()` —— 否则它与 `auth_client` 同用时库无表（Task 3 Step 2 实测） |
| `tests/test_review_compose.py`（新建） | compose 端点契约 + 外部输入校验 + 陈旧 layout 守卫 |
| `tests/test_review_preview.py`（新建） | preview 端点契约 |
| `tests/test_confirm_transport.py`（改） | 追加「confirm 会规范化」用例（既有「一字不差」用例必须保持绿） |
| `tests/test_review_layout_routes.py`（改） | 断言 `/layout` 下发 `editable_labels` |
| `scripts/probe_spec_ui.py`（改） | 新增 **f13** 组（行编辑 / 行内富文本 / 对齐 / 切分预览 / 提交 / 只读护栏） |

**为什么 `normalize_md` 放 `ocr_clean.py`**：它和 `clean_ocr_text` 是同一件事的两个时点 —— 后者是**审查前**的保守清洗，前者是**提交时**的格式规范化（都是「OCR 遗留格式的处置」）。放同一文件，规则看得见彼此。**为什么行内转换器单独成文件**：它是纯函数、无 DOM 依赖（除入参元素），探针能直接 `page.evaluate` 调用它逐条断言 —— 本仓没有 JS 测试设施（无 `package.json`），探针是唯一的 JS 测试宿主。

---

### Task 1: 存储期规范化 `normalize_md`

**Files:**
- Modify: `app/parser/ocr_clean.py`
- Create: `tests/test_ocr_normalize.py`

**Interfaces:**
- Produces: `normalize_md(md_text: str) -> str`
  - 做两件事：① 每个 `<table…>` 补 `align="center"`（已有 `align=` 或 `margin-left:auto` 的不动）；② 字面两字符 `\n` → `<br>`
  - **幂等**：`normalize_md(normalize_md(x)) == normalize_md(x)`
  - **不改行数**：`x.count("\n") == normalize_md(x).count("\n")`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_ocr_normalize.py`：

```python
"""存储期规范化（spec §8.6 方案 b / §10.4，计划 Task 1）。

两个判据（spec §10.3 的「步 2」）：
- 规范化**不改行数** ⇒ 不可能改变 `parse_markdown` 的切分
- 在**真实夹具**上：规范化前后 `clause_no` 序列**完全相同**
"""
from pathlib import Path

import pytest

from app.parser.md_parser import parse_markdown
from app.parser.ocr_clean import normalize_md

REPO = Path(__file__).resolve().parent.parent
REF_MD = REPO / "data" / "_ocr_reference" / "reference.md"
BS = chr(92)                      # 反斜杠：不写字面两字符，避开各处转义
LIT_N = BS + "n"


def test_table_without_align_gets_centered():
    assert normalize_md("<table><tr><td>a</td></tr></table>") == \
        '<table align="center"><tr><td>a</td></tr></table>'


def test_table_with_attributes_keeps_them():
    out = normalize_md('<table class="x" style="width:100%"><tr></tr></table>')
    assert out.startswith('<table align="center"')
    assert 'class="x"' in out and 'style="width:100%"' in out


def test_table_already_centered_is_untouched():
    """已带 align= 或 margin auto 的表格不动 —— 这是幂等的前提"""
    a = '<table align="center"><tr></tr></table>'
    b = '<table style="margin-left:auto;margin-right:auto"><tr></tr></table>'
    assert normalize_md(a) == a
    assert normalize_md(b) == b


def test_literal_backslash_n_becomes_br():
    assert normalize_md("甲" + LIT_N + "乙") == "甲<br>乙"


def test_normalize_is_idempotent():
    src = "<table><tr><td>甲" + LIT_N + "乙</td></tr></table>"
    once = normalize_md(src)
    assert normalize_md(once) == once


def test_normalize_never_changes_line_count():
    src = "<table><tr><td>甲</td></tr></table>\n3.0.1 条文" + LIT_N + "续"
    assert normalize_md(src).count("\n") == src.count("\n")


def _require_ref() -> None:
    if REF_MD.is_file():
        return
    import os
    if os.environ.get("REQUIRE_LAYOUT_FIXTURE") == "1":
        pytest.fail("缺少真实夹具：P2 验收不得空过")
    pytest.skip("缺少真实夹具 data/_ocr_reference/reference.md")


def test_fixture_has_tables_to_normalize():
    """前置护栏：夹具里确实有表格。否则下一条用例是**空转恒绿**。"""
    _require_ref()
    assert "<table" in REF_MD.read_text(encoding="utf-8")


def test_clause_sequence_unchanged_on_real_fixture():
    """★ 在真实夹具上：规范化前后 clause_no 序列必须**逐个相同**（spec §10.3 步 2）"""
    _require_ref()
    md = REF_MD.read_text(encoding="utf-8")
    before = [c["clause_no"] for c in parse_markdown(md)]
    after = [c["clause_no"] for c in parse_markdown(normalize_md(md))]
    assert after == before
    assert len(before) > 100, f"夹具条文数异常：{len(before)}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_normalize.py -v`
Expected: FAIL —— `ImportError: cannot import name 'normalize_md'`

- [ ] **Step 3: 实现 `normalize_md`**

在 `app/parser/ocr_clean.py` 末尾追加（文件顶部已有 `import re`）：

```python
# ═══════════════════════════════════════════════════════════
# 提交时的存储期规范化（审查页 P2；spec §8.6 方案 b / §10.4）
# ═══════════════════════════════════════════════════════════

# 字面两字符：反斜杠 + n。Paddle 的已知输出缺陷（真实产物 15 份里 14 份含，合计 827 处）。
# 渲染层（md-render.js）早已把它当 <br> 显示；这里把**渲染期的补救前移成存储期一次性修好**。
_LITERAL_BACKSLASH_N = "\\" + "n"

# `<table>` 开头标签：只命中「还没有居中表达」的那些 ⇒ 天然幂等。
# 居中写法二选一即可：`align="center"`（DOMPurify 保留，spec §8.6 实测）或 margin auto。
_TABLE_OPEN_RE = re.compile(
    r"<table(?![^>]*\balign=)(?![^>]*margin-left\s*:\s*auto)([^>]*)>",
    re.IGNORECASE,
)


def _center_table_tag(m: re.Match) -> str:
    return f'<table align="center"{m.group(1)}>'


def normalize_md(md_text: str) -> str:
    """审查提交时的**存储期规范化**（幂等；**不改行数**）。

    ① **所有表格统一居中**（spec §8.6 方案 b：居中是本项目的表格规范，含未编辑的表格）；
    ② **字面 `\\n` → `<br>`**（spec §10.4：行内换行，**不新增行**，故对切分零影响）。

    ⚠️ **两项都不新增/删除换行符** —— 这是「规范化不改切分」的结构性保证，
    由 `tests/test_ocr_normalize.py` 的行数断言 + 真实夹具的 clause_no 序列断言共同钉住。

    ⚠️ **不得**用于存量规范的清洗：那会改派生文本（`plain_text` 会把 `<br>` 当块级标签
    转换行），必须连带重建索引 ⇒ 属独立议题（spec §10.4 / R6）。本函数只在新导入路径用。
    """
    if not md_text:
        return md_text
    out = md_text.replace(_LITERAL_BACKSLASH_N, "<br>")
    return _TABLE_OPEN_RE.sub(_center_table_tag, out)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_ocr_normalize.py -v`
Expected: PASS（8 条；含真实夹具两条）

- [ ] **Step 5: 类型检查**

Run: `pyright app/parser/ocr_clean.py tests/test_ocr_normalize.py`
Expected: 0 errors（不得新增 error）

- [ ] **Step 6: 提交**

```bash
git add app/parser/ocr_clean.py tests/test_ocr_normalize.py
git commit -m "feat: 提交期规范化 normalize_md（表格居中 + 字面 n 转 br，幂等不改行数）"
```

---

### Task 2: 行内转换器 `md-inline.js`（纯函数）

**Files:**
- Create: `static/components/md-inline.js`
- Modify: `app/templates/base.html`（引入脚本）、`scripts/probe_spec_ui.py`（f13 骨架 + 3 条纯函数用例）

**Interfaces:**
- Produces（挂 `window.mdInline`）：
  - `splitLines(md: string) -> string[]`
  - `parseLine(md: string) -> {align: string|null, inner: string}` —— 解对齐包裹（M9 的 span 形式），无包裹时 `align=null`
  - `lineToHtml(inner: string) -> string` —— 行内 md → 可编辑 HTML（成对标记识别，**孤立 `*` 原样保留**）
  - `domToMd(el: Element) -> string` —— DOM → 行内 md（`<br>` ⇒ 字面 `\n`）
  - `wrap(inner: string, align: string|null) -> string` —— 加/不加对齐包裹
- **不变式（探针 f13_2 钉）**：`domToMd(容器.innerHTML = lineToHtml(x)) === x`，对含 `**粗**`、`~~删~~`、孤立 `*`、字面 `\n`、`&` `<` 的行都成立

- [ ] **Step 1: 写实现**

创建 `static/components/md-inline.js`：

```js
/* 行内 Markdown ↔ DOM 转换器（OCR 审查页 P2）
 *
 * 为什么单独成文件：本仓没有 JS 测试设施（无 package.json），**探针是唯一的 JS 测试宿主**
 * —— 纯函数才能被 page.evaluate 逐条断言。
 *
 * 设计要点（全部有实测依据，见计划 §M9/M10）：
 * - **只认成对标记**：`**…**` / `~~…~~` / `*…*`（斜体内部不再出现 `*`）。
 *   孤立的 `*`（真实文本里就有，如「3.0.1 采用 * 号标注」）必须**原样保留**，
 *   否则「进编辑再保存」会把用户没改的文本改坏。
 * - **成对输出**：删除线只在两侧都输出 `~~`，**绝不输出单个 `~`**（本仓 2026-10-02 修过该缺陷）。
 * - **不做 HTML 转义**：文本原样进出，保证「进编辑态后直接保存」**逐字节不变**。
 *   渲染侧的安全由既有 marked + DOMPurify 兜底（与源码审查页同一风险面，spec §14）。
 */
(function () {
    const BS = String.fromCharCode(92);          // 反斜杠，避开本文件里的转义歧义
    const LIT_N = BS + 'n';                      // 字面两字符：反斜杠 + n

    // 成对标记：粗体 / 删除线 / 斜体。斜体内部不允许再出现 `*`（避免与粗体抢边界）。
    const BOLD = /\*\*(?=\S)([\s\S]*?\S)\*\*/;
    const STRIKE = /~~(?=\S)([\s\S]*?\S)~~/;
    const ITALIC = /\*(?=\S)([^*]*?\S)\*/;

    // 对齐包裹（M9 形式）：同行包裹，**不新增行**；span 不是 HTML 块标签 ⇒ 行内 markdown 仍被解析。
    const ALIGN_RE = new RegExp(
        '^<span style="display:block;text-align:(left|center|right)">([\\s\\S]*)</span>$');

    /** 行内 md → 片段树：{text, marks:{b,i,s}} 或 {br:true}。只认成对标记。 */
    function toRuns(text) {
        const runs = [];
        let buf = '';
        const flush = (marks) => { if (buf) { runs.push({ text: buf, marks: marks }); buf = ''; } };
        const scan = (s, marks) => {
            while (s.length) {
                let m = BOLD.exec(s);
                let key = 'b';
                const s2 = STRIKE.exec(s);
                if (s2 && (!m || s2.index < m.index)) { m = s2; key = 's'; }
                const i2 = ITALIC.exec(s);
                if (i2 && (!m || i2.index < m.index)) { m = i2; key = 'i'; }
                if (!m) { buf += s; break; }
                buf += s.slice(0, m.index);
                flush(marks);
                const next = Object.assign({}, marks);
                next[key] = true;
                scan(m[1], next);                 // 递归：嵌套标记
                s = s.slice(m.index + m[0].length);
            }
            flush(marks);
        };
        // 字面 \n 先切成硬换行，再逐段扫标记
        text.split(LIT_N).forEach((part, i) => {
            if (i) runs.push({ br: true, marks: { b: false, i: false, s: false } });
            if (part) scan(part, { b: false, i: false, s: false });
        });
        // 合并相邻同标记片段（保证 emit 时不留碎块）
        const merged = [];
        for (const r of runs) {
            const last = merged[merged.length - 1];
            const same = last && !last.br && !r.br && last.marks.b === r.marks.b
                && last.marks.i === r.marks.i && last.marks.s === r.marks.s;
            if (same) last.text += r.text; else merged.push(r);
        }
        return merged;
    }

    /** 片段树 → 行内 md。**先全关再全开** ⇒ 标记必定成对，不产生悬空标记。 */
    function emitRuns(runs) {
        let out = '';
        let prev = { b: false, i: false, s: false };
        for (const r of runs) {
            if (r.br) { out += LIT_N; continue; }
            const cur = r.marks || { b: false, i: false, s: false };
            if (cur.b !== prev.b || cur.i !== prev.i || cur.s !== prev.s) {
                if (prev.b) out += '**';
                if (prev.i) out += '*';
                if (prev.s) out += '~~';
                if (cur.b) out += '**';
                if (cur.i) out += '*';
                if (cur.s) out += '~~';
            }
            out += r.text;
            prev = cur;
        }
        if (prev.b) out += '**';
        if (prev.i) out += '*';
        if (prev.s) out += '~~';
        return out;
    }

    function makeNode(run) {
        if (run.br) return document.createElement('br');
        let node = document.createTextNode(run.text);
        if (run.marks.s) { const w = document.createElement('s'); w.appendChild(node); node = w; }
        if (run.marks.i) { const w = document.createElement('i'); w.appendChild(node); node = w; }
        if (run.marks.b) { const w = document.createElement('strong'); w.appendChild(node); node = w; }
        return node;
    }

    window.mdInline = {
        LIT_N: LIT_N,

        splitLines(md) { return String(md == null ? '' : md).split('\n'); },

        parseLine(md) {
            const m = ALIGN_RE.exec(String(md));
            return m ? { align: m[1], inner: m[2] } : { align: null, inner: String(md) };
        },

        wrap(inner, align) {
            return align ? '<span style="display:block;text-align:' + align + '">' + inner + '</span>'
                         : inner;
        },

        lineToHtml(inner) {
            const frag = document.createDocumentFragment();
            for (const r of toRuns(String(inner))) frag.appendChild(makeNode(r));
            const box = document.createElement('div');
            box.appendChild(frag);
            // ⚠️ **空行绝不能用 `<br>` 占位** —— DOM 里 `<br>` 会被 domToMd 还原成**字面 \n**，
            // 于是「进入编辑态 → 直接保存」会把空行变成两个可见字符。空行的可落点高度
            // 由 CSS `min-height` 提供（`.rvb-line`）。
            return box.innerHTML;
        },

        domToMd(el) {
            // Chrome 在空的可编辑行里可能留一个占位 <br> ⇒ **整行只有一个 <br> 视为空行**
            // （与 lineToHtml 的空行表示对应）。字面 \n 只出现在**非空**行内。
            if (el.childNodes.length === 1 && el.firstChild.nodeName === 'BR') return '';
            const runs = [];
            const walk = (node, marks) => {
                if (node.nodeType === 3) { runs.push({ text: node.nodeValue, marks: marks }); return; }
                if (node.nodeType !== 1) return;
                const tag = node.nodeName.toLowerCase();
                if (tag === 'br') { runs.push({ br: true, marks: marks }); return; }
                const next = Object.assign({}, marks);
                if (tag === 'b' || tag === 'strong') next.b = true;
                else if (tag === 'i' || tag === 'em') next.i = true;
                else if (tag === 's' || tag === 'strike' || tag === 'del') next.s = true;
                for (const c of node.childNodes) walk(c, next);
            };
            for (const c of el.childNodes) walk(c, { b: false, i: false, s: false });
            return emitRuns(runs);
        },
    };
})();
```

- [ ] **Step 2: 引入脚本并递增版本号**

`app/templates/base.html`：在 `review-blocks.js` **之前**插入（顺序无关正确性，但读起来自洽）：

```html
    <script src="/static/components/md-inline.js?v=1"></script>
    <script src="/static/components/review-blocks.js?v=2"></script>
```

（`review-blocks.js` 由 `?v=1` → `?v=2`；`app.css` 由 `?v=38` → `?v=39`。）

- [ ] **Step 3: 写探针 f13 骨架 + 3 条纯函数用例**

在 `scripts/probe_spec_ui.py` 的 f12 组之后追加：

```python
# ═══════════════════════════════════════════
# f13：版面块审查页（P2 编辑能力）
# ═══════════════════════════════════════════
#
# 复用 f12 的 _rvb_seed（造带 layout.json 的待审查任务）。
# 本组用例一律**先进入编辑态再断言**；纯函数用例直接 evaluate window.mdInline。
#
# ⚠️ 本仓无 JS 测试设施 ⇒ 探针是纯函数唯一的宿主；改 md-inline.js 必须同步跑 f13。


```

```python
def f13_1_md_inline_loaded_and_split_lines(page):
    """前置：md-inline.js 已加载（否则后面全是空跑）"""
    page.goto(BASE)
    assert page.evaluate("() => !!(window.mdInline && window.mdInline.splitLines)"), \
        "md-inline.js 未加载（base.html 未引入或版本号没递增）"
    assert page.evaluate("() => window.mdInline.splitLines('a\\nb\\nc').length") == 3


def f13_2_inline_round_trip_is_byte_exact(page):
    """★ 纯函数不变式：domToMd(lineToHtml(x)) === x（含强调、字面 \\n、孤立星号、&<>）"""
    page.goto(BASE)
    cases = [
        "3.0.1 普通一行",
        "**粗体**与*斜体*与~~删除线~~",
        "3.0.1 采用 * 号标注",          # 孤立星号：必须原样保留
        "甲" + chr(92) + "n乙",         # 字面反斜杠 n
        "a & b < c > d",                # 不做 HTML 转义 ⇒ 逐字节可逆
        "",                             # 空行：不得用 <br> 占位，否则回来变成字面 \n
    ]
    for x in cases:
        got = page.evaluate("""(x) => {
            const box = document.createElement('div');
            box.innerHTML = window.mdInline.lineToHtml(x);
            return window.mdInline.domToMd(box);
        }""", x)
        assert got == x, f"行内往返被改坏：{x!r} → {got!r}"
    # ★ **方向钉**（Task 2 评审的 Important 发现，已裁定采纳）：
    #   `domToMd(lineToHtml(x)) === x` 只证明两者**互为逆函数** —— 一对
    #   「lineToHtml 原样返回文本 / domToMd 取 textContent」的错实现同样恒绿，
    #   即整条标记方向可以完全失守而本用例不动。spec §15.3 明确要求这张网**非空**
    #   （摘掉能力必须变红），故必须再直接钉住两个方向。
    html = page.evaluate("() => window.mdInline.lineToHtml('**粗** 和 ~~删~~ 与 *斜*')")
    assert "<strong>" in html and "<s>" in html and "<i>" in html, \
        f"标记没有转成真实标签（行内 md → HTML 方向失守）：{html!r}"
    back = page.evaluate("""() => {
        const box = document.createElement('div');
        box.innerHTML = '<strong>a</strong><s>b</s><i>c</i>';
        return window.mdInline.domToMd(box);
    }""")
    assert back == "**a**~~b~~*c*", f"手写 HTML 未转回标记（DOM → md 方向失守）：{back!r}"
    # Chrome 会在空的可编辑行里留占位 <br>：整行只有一个 <br> 必须被当成**空行**
    got = page.evaluate("""() => {
        const box = document.createElement('div'); box.innerHTML = '<br>';
        return window.mdInline.domToMd(box);
    }""")
    assert got == "", f"占位 <br> 被当成了字面换行：{got!r}"


def f13_3_alignment_wrap_unwrap_round_trip(page):
    """对齐包裹/解包往返；且包裹**不新增行**（M9：绝不引入换行符）"""
    page.goto(BASE)
    got = page.evaluate("""() => {
        const M = window.mdInline;
        const line = M.wrap('图 8.0.2 (mm)', 'center');
        const parsed = M.parseLine(line);
        return {line: line, align: parsed.align, inner: parsed.inner};
    }""")
    assert got["align"] == "center", f"解包未取回对齐档：{got}"
    assert got["inner"] == "图 8.0.2 (mm)", f"解包未取回内容：{got}"
    assert "\n" not in got["line"], "对齐包裹引入了换行 —— 会改变切分"
    assert "text-align:center" in got["line"]


CASES["f13"] = [f13_1_md_inline_loaded_and_split_lines,
                f13_2_inline_round_trip_is_byte_exact,
                f13_3_alignment_wrap_unwrap_round_trip]
```

- [ ] **Step 4: 起隔离实例跑 f13 前三条**

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_spec.db
export DATABASE_PATH="$PWD/data/_probe_spec.db"
export UPLOAD_DIR="$PWD/data/_probe_uploads"
export OUTPUT_DIR="$PWD/data/_probe_outputs"
export LANCE_DB_PATH="$PWD/data/_probe_lance"
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123 &
D:/Python/python.exe scripts/probe_spec_ui.py f13
```
Expected: `== f13: 3/3 passed ==`

- [ ] **Step 5: 类型检查 + 提交**

Run: `pyright scripts/probe_spec_ui.py`
Expected: 0 errors

```bash
git add static/components/md-inline.js app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 行内 md 转换器 md-inline.js（成对标记、对齐包裹、逐字节可逆，探针 3/3）"
```

---

### Task 3: `compose` 端点（splice 接线）

**Files:**
- Modify: `app/routes/import_routes.py`
- Modify: `tests/conftest.py`（`isolated_paths` 补 `init_db()`，理由见 Step 2）
- Create: `tests/test_review_compose.py`

**Interfaces:**
- Consumes: `app.ocr.layout.load_layout` / `splice` / `base_sha256`、`app.parser.ocr_clean.normalize_md`
- Produces: `POST /import/review/{task_id}/compose`
  - 请求体：`{"edits": {"<块下标>": "<新内容>"}}`（下标 = `layout.json` 的 `blocks` 数组下标，与 P1 前端 `__i` 同一空间）
  - 成功：`200 {"md_text": "<拼装并规范化后的完整 md>"}`
  - `400` body 非 JSON · `404` 任务/版面缺失 · `409` 版面与文本不一致 · `413` 过大 · `422` edits 非法
- Produces（模块内，供 Task 4 复用）：`async def _compose_or_error(request, task_id) -> tuple[str | None, JSONResponse | None]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_compose.py`：

```python
"""compose 端点（计划 Task 3）。

判据来自 spec §10.1（拼装规则单一来源）与 §14（外部输入校验）：
- 不编辑 ⇒ 结果 == `normalize_md(原 md)`，且**去掉规范化后逐字节等于原 md**
- 编辑 ⇒ 只有被编辑的那一段变了
- 外部输入（块下标、值类型、body 大小、JSON 合法性）逐项校验，**断言 detail 文案**
  （只查状态码会空转：路由不存在时 FastAPI 也回 404，本仓踩过）
"""
import json
from pathlib import Path

from app.ocr.layout import anchor_blocks, base_sha256, save_layout
from app.parser.ocr_clean import normalize_md

MD = "3.0.1 第一条。\n\n3.0.2 第二条。\n\n3.0.3 第三条。"


def _blocks_of(md: str) -> list[dict]:
    """三块，**块内容含编号**。

    ⚠️ **必须含编号**：`anchor_blocks` 要求「块内容 == 该行内容」（`_split_affixes` 只剥
    `#` 前缀与 HTML 外壳）。写成 `"第一条。"` 会让**三个块全部锚不上**（实测 `start_line`
    全 None）⇒ 编辑落不到任何区间 ⇒ 编辑类用例变成「看起来在测、实际空转」。
    真实产物正是含编号的：477 个 `text` 块 **477 个锚定成功**，只有内容逐字等于整行才可能。
    """
    raw = [
        {"label": "text", "content": "3.0.1 第一条。", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 0, "global_block_id": 0},
        {"label": "text", "content": "3.0.2 第二条。", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 1, "global_block_id": 1},
        {"label": "text", "content": "3.0.3 第三条。", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 2, "global_block_id": 2},
    ]
    blocks, _ = anchor_blocks(md, raw)
    assert [b["start_line"] for b in blocks] == [0, 2, 4], \
        "前置不成立：三块必须都锚定到 0/2/4 行，否则下面的编辑用例是空转"
    return blocks


def _seed(auth_client, task_id="ab000001", md=MD, sha=None, blocks=None):
    """造一个带 layout.json 的待审查任务，返回 task_id。

    `blocks` 默认按 `md` 锚定；**不需要锚定**的场景（例如只验「规范化有没有接线」）
    显式传 `[]` —— 否则 `_blocks_of` 的前置断言会先炸。
    """
    from tests.conftest import seed_import_task
    from app.routes import import_routes as ir

    seed_import_task(task_id, status="review_needed", progress=50,
                     message="待审查", md_text=md, title="测试", code="GB-X",
                     file_path="/tmp/x.pdf", file_name="x.pdf")
    bl = _blocks_of(md) if blocks is None else blocks
    save_layout({
        "version": 1, "pages": [{"index": 1, "width": 100, "height": 200}],
        "blocks": bl, "base_sha256": sha if sha is not None else base_sha256(md),
        "stats": {},
    }, Path(ir.OUTPUT_DIR) / task_id)
    return task_id


def _post(auth_client, task_id, payload):
    return auth_client.post(f"/import/review/{task_id}/compose",
                            content=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"})


def test_compose_without_edits_keeps_text_byte_exact_before_normalize(auth_client, isolated_paths):
    """不编辑 ⇒ **未编辑区间原样取回**（splice 的结构性保证）：先去规范化再比，逐字节相等

    ⚠️ **本用例**不能**充当「规范化已接线」的证据**：本夹具的 md 既无字面反斜杠 n 也无
    `<table>` ⇒ `normalize_md` 在它上面是**恒等函数** ⇒ 把路由里那句 `normalize_md(...)`
    整句删掉，这行断言照样绿（Task 3 评审实测）。规范化由下一条用例钉住。
    """
    from app.ocr.layout import splice
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["md_text"] == normalize_md(splice(MD, _blocks_of(MD), {}))


def test_compose_applies_storage_normalization(auth_client, isolated_paths):
    """★ 组装结果必须**真的**过一遍 `normalize_md`（spec §8.6 方案 b / §10.4）。

    输入必须挑**规范化确实会改写**的那两类（字面反斜杠 n、裸 `<table>`），否则用例
    在恒等夹具上恒绿，整条落库规范化保证会**静默回归而测试全绿**（Task 3 评审的
    Important 发现，已裁定采纳）。块列表传 `[]`：本用例只验接线，不需要锚定。
    """
    md = "3.0.1 条文" + chr(92) + "n续\n<table><tr><td>甲</td></tr></table>"
    task_id = _seed(auth_client, "ab000003", md=md, blocks=[])
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    out = resp.json()["md_text"]
    assert "<br>续" in out, f"字面反斜杠 n 未在组装结果里被规范化：{out!r}"
    assert '<table align="center">' in out, f"表格未在组装结果里被规范化成居中：{out!r}"


def test_compose_applies_only_the_edited_range(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {"1": "改过的第二条。"}})
    assert resp.status_code == 200, resp.text
    out = resp.json()["md_text"]
    assert "改过的第二条。" in out
    assert "3.0.1 第一条。" in out and "3.0.3 第三条。" in out
    # ⚠️ 必须断言**整行**（含编号）已消失：新内容「改过的第二条。」**含**子串「第二条。」，
    #    只查子串会让这条断言恒真
    assert "3.0.2 第二条。" not in out


def test_compose_rejects_out_of_range_index_with_detail(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {"99": "x"}})
    assert resp.status_code == 422
    assert "越界" in resp.json()["detail"]


def test_compose_rejects_non_string_value_with_detail(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {"0": 123}})
    assert resp.status_code == 422
    assert "字符串" in resp.json()["detail"]


def test_compose_rejects_non_json_body_with_detail(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = auth_client.post(f"/import/review/{task_id}/compose", content=b"not json",
                            headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert "JSON" in resp.json()["detail"]


def test_compose_rejects_when_layout_missing(auth_client, isolated_paths):
    from tests.conftest import seed_import_task
    task_id = "ab000002"
    seed_import_task(task_id, status="review_needed", md_text=MD, title="t", code="c",
                     file_path="/tmp/x.pdf", file_name="x.pdf")
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 404
    assert "版面" in resp.json()["detail"]


def test_compose_rejects_stale_layout(auth_client, isolated_paths):
    """★ 指纹守卫：旧 span + 新文本必须 409（否则会把编辑写进错的行区间）"""
    task_id = _seed(auth_client, sha="0" * 64)
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 409
    assert "不一致" in resp.json()["detail"] or "刷新" in resp.json()["detail"]
```

- [ ] **Step 2: 先修夹具，再跑测试确认失败**

**必须先做这一步**：`tests/conftest.py` 的 `isolated_paths` 末尾补一行 `init_db()`。

为什么（2026-10-05 实测定因，Task 3 实现者先撞上并停下来问）：`auth_client` 自己就
`monkeypatch.setattr("app.database.DATABASE_PATH", …)` 到 `test_auth.db` 并 `init_db()`；
而夹具建立顺序 = 用例签名顺序，`(auth_client, isolated_paths)` 会让 `isolated_paths`
**后**建立、把 `DATABASE_PATH` 改指到**未建表**的 `t.db` ⇒ 全部用例死在
`sqlite3.OperationalError: no such table: import_tasks`，**根本到不了端点**，
RED 与「端点不存在」混为一谈。本仓既有用例**从不把这两个夹具同用**（各自的 env
夹具如 `test_review_layout_routes.py::review_env` 都是「先 auth_client、再 patch + init_db」）。
补这一行让组合与顺序无关，且只**增建表**、不改任何既有语义。

```python
    for d in ("lance_db", "uploads", "outputs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    # ⚠️ 必须建表：`auth_client` 建的是**它自己**那份 DB，本夹具随后把 DATABASE_PATH
    # 改指到这里的 t.db。不建表时两者同用会死在 no such table（曾实测踩到）。
    from app.database import init_db
    init_db()
    return tmp_path
```

Run: `D:/Python/python.exe -m pytest tests/test_review_compose.py -v`
Expected: FAIL —— 7 条**全部**失败，且失败原因是**端点还不存在**（多为 `assert 200` 实得 404；
两条 404 用例因 `detail` 文案不是「版面」而红）。**不得**出现 `no such table`。

> ⚠️ 改的是**全仓共用**的夹具 ⇒ 本 Task 的验证必须包含**全量回归**（Global Constraint 7 的增量要求在此例外）。

- [ ] **Step 3: 实现端点**

`app/routes/import_routes.py`：**顶部三处 import 必须补齐**（否则 `NameError`；已核对现状：
该文件**没有 `import json`**，`from app.ocr.layout import finalize_layout, load_layout` 里**没有
`splice`/`base_sha256`**）：

```python
import json                                  # 新增（文件顶部 import 块，紧邻 import html）
from app.parser.ocr_clean import clean_ocr_text, normalize_md        # 合并既有那一行
from app.ocr.layout import finalize_layout, load_layout, splice, base_sha256   # 合并既有那一行
```

然后在 `review_image` 之后、`confirm_review` 之前插入：

```python
# ── 编辑提交：compose（拼装）+ 其共用前置 ───────────────────────────

# 请求体上限：最重真实产物 617 892 字符（M4）按 UTF-8 中文 3 字节 ≈ 1.9 MB，
# 留 4 倍余量。超过即拒（全局规则 §1.1：外部输入必须限幅）。
_COMPOSE_MAX_BODY = 8 * 1024 * 1024
# 单块内容上限：夹具里最长的块 25 行（M3），给 1 MB 已是数量级余量。
_EDIT_VALUE_MAX = 1_000_000


def _parse_edits(payload: object, n_blocks: int) -> tuple[dict[int, str], str]:
    """把外部输入的 `edits` 变成 `{下标: 内容}`；非法时返回错误文案。

    **逐项校验**（下标是整数且在范围内、值是字符串、值不超限）—— 这是外部输入，
    不可信。返回文案而不是抛异常：路由据此回 422，且**文案里点名是哪一项**。
    """
    if not isinstance(payload, dict):
        return {}, "请求体格式不正确（需 JSON 对象）"
    raw = payload.get("edits")
    if raw is None:
        return {}, ""
    if not isinstance(raw, dict):
        return {}, "edits 必须是对象"
    edits: dict[int, str] = {}
    for k, v in raw.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            return {}, f"块下标非法：{k!r}"
        if not 0 <= idx < n_blocks:
            return {}, f"块下标越界：{idx}"
        if not isinstance(v, str):
            return {}, f"块内容必须是字符串：{idx}"
        if len(v) > _EDIT_VALUE_MAX:
            return {}, f"块内容过长：{idx}"
        edits[idx] = v
    return edits, ""


async def _compose_or_error(
    request: Request, task_id: str
) -> tuple[str | None, JSONResponse | None]:
    """compose / preview 的共用前置；成功返回 `(拼装后的 md, None)`。

    守卫顺序刻意如此：先确认任务与版面都存在，再校验请求体 —— 否则会拿一个
    「任务不存在」的现场去报「JSON 非法」，把排查方向带偏。
    """
    task = _get_task(task_id)
    if not task or task.get("md_text") is None:
        return None, JSONResponse({"detail": "任务不存在或已过期"}, status_code=404)
    layout = load_layout(Path(OUTPUT_DIR) / task_id)
    if layout is None:
        return None, JSONResponse({"detail": "无版面数据"}, status_code=404)

    body = await request.body()
    if len(body) > _COMPOSE_MAX_BODY:
        return None, JSONResponse({"detail": "提交内容过大，未处理"}, status_code=413)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, JSONResponse({"detail": "请求体不是合法 JSON"}, status_code=400)

    md_text = task["md_text"]
    blocks = layout.get("blocks") or []
    edits, err = _parse_edits(payload, len(blocks))
    if err:
        return None, JSONResponse({"detail": err}, status_code=422)

    # 指纹守卫：layout 的 span 是对**这份文本**锚定的。文本变过（例如任务被重跑）
    # 就必须拒绝，否则会把编辑写进错的行区间 —— 静默改写，正是最不能承受的失效。
    sha = layout.get("base_sha256")
    if sha and sha != base_sha256(md_text):
        return None, JSONResponse(
            {"detail": "版面数据与当前文本不一致，请刷新页面后重试"}, status_code=409)

    try:
        return normalize_md(splice(md_text, blocks, edits)), None
    except ValueError as e:
        # splice 只在区间重叠时抛（anchor_blocks 的游标保证不该发生）⇒ 记 ERROR 便于排查
        logger.error("compose 版面区间异常 task=%s: %s", task_id, e)
        return None, JSONResponse({"detail": "版面区间异常，请联系管理员"}, status_code=500)


@router.post("/import/review/{task_id}/compose")
async def review_compose(request: Request, task_id: str):
    """把前端块编辑拼装成完整 `md_text`（spec §10.1：拼装规则**只写一份**）。

    前端不自己拼：`splice` 是本仓 P0 交付、被 f12 反复验证的唯一实现，
    在 JS 里复刻一份就是本仓反复警告的「第二把尺子」。

    **本端点不写库**：结果由前端原样 POST 给既有 `confirm`（raw body）⇒ 确认链路零改动。
    """
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)
    md_text, err = await _compose_or_error(request, task_id)
    if err is not None:
        return err
    return {"md_text": md_text}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_review_compose.py -v`
Expected: PASS（8 条）

- [ ] **Step 5: 回归相关既有测试**

Run: `D:/Python/python.exe -m pytest tests/test_review_layout_routes.py tests/test_layout_anchor.py tests/test_confirm_transport.py -v`
Expected: 全绿（本 Task 未碰这三处的行为）

- [ ] **Step 6: 类型检查 + 提交**

Run: `pyright app/routes/import_routes.py tests/test_review_compose.py`
Expected: 0 errors

```bash
git add app/routes/import_routes.py tests/test_review_compose.py
git commit -m "feat: 审查页 compose 端点（splice 接线 + 指纹守卫 + 逐项输入校验）"
```

---

### Task 4: `preview` 端点（切分预览）

**Files:**
- Modify: `app/routes/import_routes.py`
- Create: `tests/test_review_preview.py`

**Interfaces:**
- Consumes: `_compose_or_error`（Task 3）、`app.parser.md_parser.parse_markdown`
- Produces: `POST /import/review/{task_id}/preview`（请求体同 compose）
  - `200 {"count": int, "clauses": [{"clause_no": str, "title": str}], "md_chars": int}`
  - **`count` = 导入后**会入库的条数：必须镜像 Phase 2 的两步 `_filter_cover_clauses(parse_markdown(md))`（见 Step 3）
  - 失败码与 compose **完全一致**（共用前置）
- 前端的「与初始相比 ±N」**由前端自己算**：页面初始化时用 `edits={}` 调一次本端点，那份结果就是基线。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_preview.py`：

```python
"""preview 端点（计划 Task 4）。

它的价值（spec §11.4）不是「显示条文」而是「**你这次编辑改了切分吗**」——
所以判据必须落在「改一条编号行 ⇒ 条数与 clause_no 序列随之变化」上，
而不是「返回了个列表」。
"""
import json

from tests.test_review_compose import MD, _seed


def _post(auth_client, task_id, payload):
    return auth_client.post(f"/import/review/{task_id}/preview",
                            content=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"})


def test_preview_returns_clause_list_of_current_md(auth_client, isolated_paths):
    task_id = _seed(auth_client, "ab000010")
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [c["clause_no"] for c in data["clauses"]] == ["3.0.1", "3.0.2", "3.0.3"]
    assert data["count"] == 3


def test_preview_mirrors_phase2_cover_filter(auth_client, isolated_paths):
    """★ 封面/出版信息页伪条文必须**同样被丢掉**。

    漏掉 `_filter_cover_clauses` 会让预览数**系统性地高于**入库数，而 spec §18 的
    P2 出口判据正是「切分预览与导入后实际条文数一致」。
    （`## 第X页` 是 OCR 管线的分隔标记，parse_markdown 会为它生成一条伪条文
     —— 见 `app/parser/ocr_clean.py` 的模块 docstring。）
    """
    from app.parser.md_parser import parse_markdown
    from app.routes.import_routes import _filter_cover_clauses

    md = ("## 第1页\nICS 77.140.60\n中华人民共和国国家标准\n代替 GB/T 1499.1—2008\n\n"
          "3.0.1 第一条。")
    assert len(parse_markdown(md)) == 2, "前置不成立：夹具没有产出封面伪条文"
    assert len(_filter_cover_clauses(parse_markdown(md))) == 1, "前置不成立：封面未被过滤"

    task_id = _seed(auth_client, "ab000014", md=md, blocks=[])
    # ⚠️ `blocks=[]` 不是可选项：本用例的 md 是**另一份**文本，默认的 `_blocks_of(md)`
    # 会因前置断言（三块须锚到 0/2/4）先炸。本用例只关心条数，不需要锚定。
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 1, "预览没有镜像 Phase 2 的封面过滤"


def test_preview_reflects_an_edit_that_adds_a_clause(auth_client, isolated_paths):
    """★ 补写一条**带编号的新行** ⇒ 预览必须多出一条（这正是用户补 OCR 丢失内容的场景）

    ⚠️ 编辑值**必须带上原行整行内容**（含 `3.0.3`）：行级编辑器里那一行就是整行，
    用户是在其**后**追加一行；只写「第三条。」会把 `3.0.3` 这个编号整行抹掉，
    切出来变成 `3.0.1/3.0.2/3.0.4`（少一条），用例会红得莫名其妙。实测已核。
    """
    task_id = _seed(auth_client, "ab000011")
    edited = "3.0.3 第三条。\n3.0.4 补写的新条文。"
    resp = _post(auth_client, task_id, {"edits": {"2": edited}})
    assert resp.status_code == 200, resp.text
    nos = [c["clause_no"] for c in resp.json()["clauses"]]
    assert nos == ["3.0.1", "3.0.2", "3.0.3", "3.0.4"], f"新增条文未被切出：{nos}"


def test_preview_rejects_bad_edit_with_same_detail_as_compose(auth_client, isolated_paths):
    """与 compose 共用前置 ⇒ 错误文案必须一致（两条端点不能各说各话）"""
    task_id = _seed(auth_client, "ab000012")
    resp = _post(auth_client, task_id, {"edits": {"99": "x"}})
    assert resp.status_code == 422
    assert "越界" in resp.json()["detail"]


def test_preview_404_without_layout(auth_client, isolated_paths):
    from tests.conftest import seed_import_task
    task_id = "ab000013"
    seed_import_task(task_id, status="review_needed", md_text=MD, title="t", code="c",
                     file_path="/tmp/x.pdf", file_name="x.pdf")
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 404
    assert "版面" in resp.json()["detail"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_review_preview.py -v`
Expected: FAIL（404 + detail 不含「版面」）

- [ ] **Step 3: 实现端点**

在 `review_compose` 之后插入：

```python
@router.post("/import/review/{task_id}/preview")
async def review_preview(request: Request, task_id: str):
    """实时切分预览（spec §11.4）：**当前编辑会切成哪些条**。

    ⚠️ **必须镜像 Phase 2 的解析前两步**，否则 spec §18 的 P2 出口判据
    「切分预览与导入后实际条文数一致」不成立：

        preview        : _filter_cover_clauses(parse_markdown(md))     ← 本端点
        _process_import_phase2: _filter_cover_clauses(parse_markdown(md_text))

    `_filter_cover_clauses` 会丢掉封面/出版信息页伪条文（`import_routes.py` 内既有），
    漏掉它会让预览数**系统性地高于**入库数，而用户拿这个数当验收凭据。

    与 compose **同源**（共用 `_compose_or_error`：同一次 splice + 同一次 normalize）
    ⇒ 这里数出来的就是「真提交上去会入库的条数」。

    性能（实测 M4）：最重真实产物 617 892 字符 ⇒ `parse_markdown` 109 ms；
    拼装 0.4 ms。故**不需要** spec §11.4 设想的降级（只显示受影响片段）；
    前端做 400 ms 防抖即可。
    """
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)
    md_text, err = await _compose_or_error(request, task_id)
    if err is not None:
        return err
    # ⚠️ 这一行**不是多余的**（Task 4 实现者实测逼出）：`_compose_or_error` 返回
    # `tuple[str | None, JSONResponse | None]` 是**两个独立 union**、不是判别联合 ⇒
    # `if err is not None` **不会**把 `md_text` 窄化成 `str`；直抄 Step 3 的代码会报
    # 2 条 `reportArgumentType`（喂给 `parse_markdown` 时）。本仓栽过**同类两次**
    # （「不做元组跨元素窄化」）。用 `assert` 而不是 `cast`：运行期零成本，
    # 且把「走到这里 md_text 必非 None」写成可执行的不变量。
    assert md_text is not None
    rows = _filter_cover_clauses(parse_markdown(md_text))
    clauses = [{"clause_no": c.get("clause_no") or "", "title": c.get("title") or ""}
               for c in rows]
    return {"count": len(clauses), "clauses": clauses, "md_chars": len(md_text)}
```

（`parse_markdown` 需在文件顶部 `from app.parser.md_parser import parse_markdown`；若已有同类 import 则合并。）

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_review_preview.py -v`
Expected: PASS（5 条）

- [ ] **Step 5: 类型检查 + 提交**

Run: `pyright app/routes/import_routes.py tests/test_review_preview.py`
Expected: 0 errors

```bash
git add app/routes/import_routes.py tests/test_review_preview.py
git commit -m "feat: 审查页 preview 端点（与 compose 同源，切分预览不吃第二把尺子）"
```

---

### Task 5: `confirm` 接规范化（覆盖两条入口）

**Files:**
- Modify: `app/routes/import_routes.py`、`app/templates/partials/ocr_review.html`
- Modify: `tests/test_confirm_transport.py`、`tests/test_templates.py`

**Interfaces:**
- Consumes: `normalize_md`（Task 1）
- 语义变化：`confirm` 收到的 md 在写入任务前**过一次 `normalize_md`**。用户 2026-10-05 裁定：**两条入口都生效**（.md 源码审查页也走这里 ⇒ 自动覆盖）。
- 幂等（Task 1 已测）⇒ 版面块页在 compose 里已规范化过一次，这里是 no-op。

- [ ] **Step 1: 写失败测试**

在 `tests/test_confirm_transport.py` 末尾追加：

```python
def test_confirm_normalizes_tables_and_literal_n(auth_client, monkeypatch):
    """★ confirm 是两条入口的**唯一咽喉** ⇒ 规范化放这里，.md 与 PDF 都覆盖。

    用户 2026-10-05 裁定「两条入口都生效」（另一选项是只管版面块页）。
    """
    import app.routes.import_routes as ir

    monkeypatch.setattr(ir, "_process_import_phase2", _phase2_stub([]))
    task_id = "cc000010"
    _seed_task(task_id)
    body = "<table><tr><td>甲</td></tr></table>\n3.0.1 条文" + chr(92) + "n续"
    try:
        resp = auth_client.post(
            f"/import/review/{task_id}/confirm",
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        assert resp.status_code == 200, resp.text
        stored = import_task_row(task_id)["md_text"]
        assert '<table align="center">' in stored
        assert "<br>续" in stored
    finally:
        ir.delete_task(task_id)


def test_confirm_leaves_plain_text_untouched(auth_client, monkeypatch):
    """没有表格、没有字面 \\n 的正文**一字不改**（规范化的影响面必须收窄到这两类）"""
    import app.routes.import_routes as ir

    monkeypatch.setattr(ir, "_process_import_phase2", _phase2_stub([]))
    task_id = "cc000011"
    _seed_task(task_id)
    body = "# 规范\n## 3.0.1 条文\n内容一段。"
    try:
        resp = auth_client.post(
            f"/import/review/{task_id}/confirm",
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        assert resp.status_code == 200
        assert import_task_row(task_id)["md_text"] == body
    finally:
        ir.delete_task(task_id)
```

在 `tests/test_templates.py` 追加一条：

```python
def test_md_review_page_discloses_submit_normalization(auth_client, isolated_paths):
    """★ 源码审查页必须**明说**提交时会做规范化。

    用户裁定两条入口都生效后，.md 用户在源码框里看不到表格被加 align —— 静默改写
    与「源码页所见即所提」相冲突，至少要让这层改写是**被披露**的。
    """
    from tests.conftest import seed_import_task
    task_id = "ab000020"
    seed_import_task(task_id, status="review_needed", md_text="# 规范\n## 1.1 条\n内容",
                     title="t", code="c", file_path="/tmp/x.md", file_name="x.md")
    resp = auth_client.get(f"/import/review/{task_id}")
    assert resp.status_code == 200
    assert "表格居中" in resp.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_confirm_transport.py tests/test_templates.py -v`
Expected: 两条新用例 FAIL（`align="center"` 不在库里 / 页面无提示文案）

- [ ] **Step 3: 实现**

`app/routes/import_routes.py` 的 `confirm_review`，在「空内容拒绝」之后、`title = task.get("title", "")` 之前插入：

```python
    # 存储期规范化（Task 1 的 normalize_md）：表格统一居中 + 字面 \n → <br>。
    # 放在 confirm 这个**唯一咽喉**上 ⇒ `.md` 源码审查页与版面块页**两条入口都覆盖**
    # （用户 2026-10-05 裁定）。幂等 ⇒ 版面块页在 compose 里已规范化过，此处是 no-op。
    content = normalize_md(content)
```

`app/templates/partials/ocr_review.html` 的确认行，把说明文案改为（保持原结构，只改文本）：

```html
        <small style="color:var(--pico-muted-color)">审查完毕后点击确认继续导入流程（解析 + 分类 + 索引）。提交时会自动统一表格居中，并把字面的 \n 转成 &lt;br&gt;（OCR 遗留格式）。</small>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/Python/python.exe -m pytest tests/test_confirm_transport.py tests/test_templates.py tests/test_ocr_review.py tests/test_import.py -v`
Expected: 全绿（含既有「超限 md 一字不差到达」用例 —— 它的正文既无表格也无字面 `\n`，规范化对它是 no-op）

- [ ] **Step 5: 类型检查 + 提交**

Run: `pyright app/routes/import_routes.py tests/test_confirm_transport.py`
Expected: 0 errors

```bash
git add app/routes/import_routes.py app/templates/partials/ocr_review.html tests/test_confirm_transport.py tests/test_templates.py
git commit -m "feat: confirm 接存储期规范化（两条审查入口都覆盖，源码页明示改写）"
```

---

### Task 6: 可编辑性白名单 + 行级编辑器骨架

**Files:**
- Modify: `app/ocr/layout.py`、`app/routes/import_routes.py`、`tests/test_review_layout_routes.py`
- Modify: `app/templates/partials/ocr_review_blocks.html`、`static/components/review-blocks.js`、`static/app.css`、`app/templates/base.html`（版本号）
- Modify: `scripts/probe_spec_ui.py`（f13 追加 4 条）

**Interfaces:**
- Produces: `app.ocr.layout.EDITABLE_TEXT_LABELS: frozenset[str]`（= `{"doc_title","paragraph_title","content","text","figure_title","vision_footnote"}`，依据 M3 实测 label）
- Produces: `/import/review/{task_id}/layout` 响应新增 `"editable_labels": [...]`
- 前端（`reviewBlocks()`）新增状态与方法：
  - 状态（**初值必须写全**，`focusIdx` 用 `-1` 表示「还没落到任何一行」）：
    `editableLabels: []`、`editingIdx: -1`、`edits: {}`、`focusIdx: -1`、`focusIsClause: false`
  - `canEdit(b) -> boolean`（**有 span** 且 label 在白名单内）
  - `contentOf(b) -> string`（有编辑用编辑，否则用原文）
  - `enterEdit(idx)` / `cancelEdit()` / `saveEdit()`
  - `_makeLine(mdLine)` / `_insertLineAfter(el)` / `_deleteLine(el)`
  - `_editBox()`（按 `[data-block-idx]` 取当前块的编辑框 —— **不得用 `x-ref`**，见 Step 1 的说明）
  - `_lineElements()` / `_lineIndex(el)` / `_focusLine()` / `isClauseLine(el)`（**DOM 元素不存 Alpine 状态**，只存下标）
  - `renderBlock(b)` 改用 `contentOf(b)`

- [ ] **Step 1: 后端白名单 + 下发**

`app/ocr/layout.py` 在 `DEFAULT_NOT_IN_MARKDOWN` 之后追加：

```python
# 审查页 P2 可**行级编辑**的 label —— **单一来源**（前端从 /layout 拿，不在 JS 里另写一份）。
# 依据：2026-10-05 实测夹具 label 分布（text 477 / paragraph_title 47 / figure_title 9 /
# content 4 / vision_footnote 3，全部有 span），其余 label 各归其期：
# display_formula → P4；table / chart → P3；image / number / formula_number → 不进 markdown（豁免）。
# 注意 `chart` 虽是纯文本 5 行，但**未锚定**（夹具 idx 522）⇒ 会被 canEdit 的 span 判据挡下。
EDITABLE_TEXT_LABELS = frozenset({
    "doc_title", "paragraph_title", "content", "text", "figure_title", "vision_footnote",
})
```

`app/routes/import_routes.py` 的 `review_layout` 响应体加一行：

```python
    return {
        "blocks": layout.get("blocks") or [],
        "pages": layout.get("pages") or [],
        "stats": layout.get("stats") or {},
        "editable_labels": sorted(EDITABLE_TEXT_LABELS),
    }
```

（import 处补 `EDITABLE_TEXT_LABELS`。）

`tests/test_review_layout_routes.py` 追加：

```python
def test_layout_endpoint_ships_editable_labels(review_env):
    """★ 可编辑 label 白名单必须由**后端下发**（单一来源），前端不得另写一份"""
    from app.ocr.layout import EDITABLE_TEXT_LABELS

    client, out = review_env          # 本文件既有 fixture（隔离四路径 + create_task）
    _write_layout(out)                # 本文件既有播种 helper
    r = client.get(f"/import/review/{TASK_ID}/layout")
    assert r.status_code == 200
    assert r.json()["editable_labels"] == sorted(EDITABLE_TEXT_LABELS)
    assert "text" in r.json()["editable_labels"]
```

- [ ] **Step 2: 前端骨架**

`static/components/review-blocks.js`：

```js
/* 后端契约（P2 追加）：
 *   GET  /import/review/{task_id}/layout  → {blocks, pages, stats, editable_labels}
 *   POST /import/review/{task_id}/compose  {edits:{下标:内容}} → {md_text}
 *   POST /import/review/{task_id}/preview  同上 → {count, clauses:[{clause_no,title}]}
 * 编辑**不落库**：「保存」只写 this.edits（spec §8.1）。
 */
```

状态与方法（在既有对象上追加；`init()` 里补 `this.editableLabels = data.editable_labels || []`）：

```js
        /** 可编辑 = **有行区间**（能安全回写）且在 label 白名单内（后端下发） */
        canEdit(b) {
            return this.hasSpan(b) && this.editableLabels.indexOf(b.label) >= 0;
        },

        /** 块的当前内容：有编辑用编辑，否则用 OCR 原文（渲染与编辑共用同一取值）。
         *
         * ⚠️ **必须先读一次 `this.edits[i]`**（Task 6 实现者仪表化实测逼出）：Alpine 的响应式
         * **不追踪 `hasOwnProperty`**（它走 `[[GetOwnProperty]]`，Vue 的 proxy 不拦），而首次渲染
         * 走的是 `b.content` 分支、**从没读过这个键** ⇒ 没有任何 effect 依赖它 ⇒
         * `saveEdit` 写入后 `x-html="renderBlock(b)"` 与 `x-show="isEdited(b)"` **都不会重跑**
         * （实测：edits 已写入、editingIdx 已归零、无 pageerror，而 DOM 仍是原文）。
         * 那一行 `const edited = …` 就是用来建立**键级依赖**的，不能省。
         */
        contentOf(b) {
            const i = b.__i;
            const edited = this.edits[i];                 // 先读一次 ⇒ 键级响应式依赖
            return Object.prototype.hasOwnProperty.call(this.edits, i) ? edited : (b.content || '');
        },

        /** 同理：读键本身就是依赖（不要写成 hasOwnProperty 形式） */
        isEdited(b) {
            return this.edits[b.__i] !== undefined;
        },

        enterEdit(idx) {
            const b = this.blocks[idx];
            if (!b || !this.canEdit(b)) return;
            this.editingIdx = idx;
            this.$nextTick(() => {
                const box = this._editBox();
                box.innerHTML = '';
                for (const ln of window.mdInline.splitLines(this.contentOf(b))) {
                    box.appendChild(this._makeLine(ln));
                }
                const first = box.querySelector('.rvb-line');
                this.focusIdx = 0;
                this.focusIsClause = first ? this.isClauseLine(first) : false;
                if (first) first.focus();
            });
        },

        /**
         * 一行 = 一个 contenteditable。**行结构即 DOM 结构** ⇒ 进出编辑态行数不变（spec R10）。
         *
         * ⚠️ **DOM 元素绝不放 Alpine 状态**：Alpine 会把状态对象包成 Proxy，存进去的元素
         * 再 `.focus()` 会因 `this` 不是真实元素而抛 Illegal invocation。状态里只存
         * **下标 + 布尔**（focusIdx / focusIsClause），元素每次按需从 DOM 取 —— 顺带解决
         * 「`document.activeElement` 不是响应式依赖 ⇒ 按钮 disabled 状态不更新」这个坑。
         */
        _makeLine(mdLine) {
            const parsed = window.mdInline.parseLine(mdLine);
            const el = document.createElement('div');
            el.className = 'rvb-line';
            el.setAttribute('contenteditable', 'true');
            el.dataset.align = parsed.align || '';
            el.style.textAlign = parsed.align || '';
            el.innerHTML = window.mdInline.lineToHtml(parsed.inner);
            el.addEventListener('focus', () => {
                this.focusIdx = this._lineIndex(el);
                this.focusIsClause = this.isClauseLine(el);
            });
            el.addEventListener('input', () => {
                if (this._lineIndex(el) === this.focusIdx) this.focusIsClause = this.isClauseLine(el);
            });
            el.addEventListener('keydown', (e) => this._onLineKey(e, el));
            el.addEventListener('paste', (e) => this._onPaste(e, el));
            return el;
        },

        /** 编辑框**按块下标**取 —— `x-ref` 放在 `x-for` 里会被同名的**最后一项**覆盖。
         *
         * ⚠️ **本 helper 是必需的，不是优雅写法**（Task 6 实现者用 Alpine 源码 + 最小复现实测）：
         * Alpine 把 `x-ref` 注册在**最近的 `[x-data]` 根**上（`_x_refs[名字] = 元素`），
         * 故 `x-for` 每次迭代都覆盖同一个键 ⇒ `$refs.editBox` 永远指向**最后一个块**的编辑框。
         * 直接用它会出现「点第 N 块、内容却灌进最后一块」，而 `_rvb_first_editable` 返回的正是
         * **第一个**可编辑块 ⇒ f13_4/5/6 全红。
         */
        _editBox() {
            const list = this.$refs.list;
            return list
                ? list.querySelector(`[data-block-idx="${this.editingIdx}"] .rvb-lines`)
                : null;
        },

        _lineElements() {
            const box = this._editBox();
            return box ? [...box.querySelectorAll('.rvb-line')] : [];
        },

        _lineIndex(el) {
            const box = this._editBox();
            return box ? [...box.querySelectorAll('.rvb-line')].indexOf(el) : -1;
        },

        _focusLine() {
            const els = this._lineElements();
            if (this.focusIdx >= 0 && this.focusIdx < els.length) return els[this.focusIdx];
            return els[0] || null;
        },

        /** 行首是编号或 `#` 标题 ⇒ 该行**不得被包裹**（实测：标签与内容同行会让全篇切不出条文）。
         *
         * ⚠️ **本 Task 就必须定义它**（Task 6 实现者实测逼出）：`enterEdit` 与 `_makeLine`
         * 都要调它来维护 `focusIsClause`，而 Task 7 才用它做对齐禁用。若留到 Task 7 定义，
         * Task 6 照抄会在 `$nextTick` 回调里抛 `TypeError` —— **且探针看不见**（编辑行已经
         * append 进去了，f13_4..7 照绿）。这正是本仓最怕的静默失效形态。
         */
        isClauseLine(el) {
            const t = (el.textContent || '').trim();
            return /^#{1,6}\s+/.test(t) || /^\d+(\.\d+)*\s*[．.、]?\s*\S/.test(t);
        },

        _insertLineAfter(el) {
            const line = this._makeLine('');
            el.parentNode.insertBefore(line, el.nextSibling);
            line.focus();
        },

        _deleteLine(el) {
            const box = this._editBox();
            if (box.querySelectorAll('.rvb-line').length <= 1) { el.innerHTML = ''; return; }
            const prev = el.previousElementSibling;
            el.remove();
            if (prev) prev.focus();
        },

        /** Enter ⇒ **新增一行**（绝不产生行内 <br>：真实换行与字面 \\n 在 DOM 里无法区分） */
        _onLineKey(e, el) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this._insertLineAfter(el);
                return;
            }
            if (e.key === 'Backspace' && el.textContent === '' &&
                el.previousElementSibling) {
                e.preventDefault();
                this._deleteLine(el);
            }
        },

        /** 粘贴**只取纯文本**（外部 HTML 一律丢弃），多行按行拆分落到多个编辑行 */
        _onPaste(e, el) {
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text/plain') || '';
            const parts = text.replace(/\r\n?/g, '\n').split('\n');
            document.execCommand('insertText', false, parts[0]);
            let host = el;
            for (const p of parts.slice(1)) {
                const line = this._makeLine(p);
                host.parentNode.insertBefore(line, host.nextSibling);
                host = line;
            }
            if (host !== el) host.focus();
        },

        saveEdit() {
            const box = this._editBox();
            const els = [...box.querySelectorAll('.rvb-line')];
            const md = els.map((el) => window.mdInline.wrap(
                window.mdInline.domToMd(el), el.dataset.align || null)).join('\n');
            this.edits[this.blocks[this.editingIdx].__i] = md;
            this.editingIdx = -1;
        },

        cancelEdit() {
            this.editingIdx = -1;
        },
```

`renderBlock(b)` 改为：

```js
        renderBlock(b) {
            try {
                return window.mdRender.renderHtml(this.contentOf(b),
                    '/import/review/' + this.taskId + '/');
            } catch (e) {
                return '<p style="color:#c00">渲染错误</p>';
            }
        },
```

- [ ] **Step 3: 模板**

`partials/ocr_review_blocks.html` 的块卡片体，把只读体改为「只读体 + 编辑入口 + 编辑框」：

```html
                        <!-- 只读态：编辑入口只在**可编辑块**上出现（spec §8.1 的「纠正」） -->
                        <div class="rvb-block-bd" x-show="editingIdx !== i" x-html="renderBlock(b)"></div>
                        <div class="rvb-block-meta" x-show="editingIdx !== i">
                            <button type="button" class="outline rvb-fix" x-show="canEdit(b)"
                                    @click.stop="enterEdit(i)">纠正</button>
                            <span class="rvb-edited" x-show="isEdited(b)">已修改</span>
                            <!-- 不可编辑必须**明说原因**，不得静默隐藏（Global Constraint 6） -->
                            <span class="rvb-locked" x-show="!canEdit(b)"
                                  x-text="hasSpan(b) ? '该块类型暂不支持编辑' : '原文件未定位，无法安全回写'"></span>
                        </div>
                        <!-- 编辑态：行级编辑器（行 = contenteditable） -->
                        <div class="rvb-editor" x-show="editingIdx === i" x-cloak>
                            <div class="rvb-tools">
                                <span>行级编辑</span>
                                <button type="button" @click="cancelEdit()" style="margin:0">取消</button>
                                <button type="button" @click="saveEdit()" style="margin:0">保存</button>
                            </div>
                            <!-- ⚠️ **不要加 `x-ref`**：它在 `x-for` 里会被同名的最后一项覆盖
                                 （Alpine 把 x-ref 注册在最近的 [x-data] 根上）。定位走
                                 `_editBox()` 的 `[data-block-idx]` 查询（块卡片上已有该属性）。 -->
                            <div class="rvb-lines"></div>
                        </div>
```

- [ ] **Step 4: 样式**

`static/app.css` 追加（沿用既有 `rvb-` 前缀；原语与「控件等高」约定见 memory `ui-preference-zh-align`）：

```css
.rvb-line { padding: 2px 6px; border: 1px solid transparent; border-radius: 4px; min-height: 1.6em; }
.rvb-line:focus { outline: none; border-color: var(--pico-primary); background: #fff; }
.rvb-tools { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.5rem; }
.rvb-lines { border: 1px solid var(--pico-muted-border-color); border-radius: 6px; padding: 0.25rem; }
.rvb-fix { margin: 0; font-size: 0.8rem; }
.rvb-edited { color: #c08552; font-size: 0.8rem; }
.rvb-locked { color: var(--pico-muted-color); font-size: 0.8rem; }
```

`base.html`：`review-blocks.js?v=2 → ?v=3`，`app.css?v=39 → ?v=40`。

- [ ] **Step 5: 探针 4 条**

```python
# ⚠️ **编辑框在 `x-for` 内** ⇒ 每个块各有一份 `.rvb-editor` / `.rvb-lines`（靠 `x-show` 隐藏）。
#    所以断言**必须按块作用域**取：`page.locator(".rvb-line")` 会拿到**第 0 个块**的编辑
#    （不论是否可见），点它的按钮 = 点一个 display:none 的元素，Playwright 直接失败。

def _rvb_block(page, idx):
    return page.locator(".rvb-block").nth(idx)


def _rvb_edit(page, idx):
    """进入第 idx 个块的编辑态；返回**该块**的编辑框 locator"""
    blk = _rvb_block(page, idx)
    blk.locator(".rvb-fix").click()
    assert poll_until(page, lambda: blk.locator(".rvb-lines").is_visible(), 5000), \
        "进入编辑态后编辑框未出现"
    return blk.locator(".rvb-lines")


def _rvb_type_line(page, idx, text):
    """把编辑中块的**第一行**整体换成 text，并全选该行内容（execCommand 需要选区）。

    ⚠️ **必须显式补发 `input` 事件**（Task 7 实现者实测逼出）：`el.textContent = t` 是
    **程序化赋值**，浏览器**不触发 `input`**；而该行在 `enterEdit` 里已经 `focus()` 过、
    本来就是 `activeElement`，故这里的 `el.focus()` 与随后的 Playwright `.click()`
    **都不再触发 `focus`**。后果：组件靠 focus/input 维护的 `focusIsClause` 会**永远停在
    enterEdit 时算出的值** ⇒ 断言「编号行上对齐按钮禁用」的用例（f13_10）**恒红**，
    而且与实现对不对**无关**（它失去分辨力，变异证据也随之失效）。
    真实用户的键盘输入是会触发 `input` 的 ⇒ 补发才是**忠实模拟**，不是为过用例而放宽。
    """
    page.evaluate("""([i, t]) => {
        const el = document.querySelectorAll('.rvb-block')[i].querySelector('.rvb-line');
        el.textContent = t;
        el.dispatchEvent(new Event('input', {bubbles: true}));   // 忠实模拟键盘输入
        el.focus();
        const r = document.createRange(); r.selectNodeContents(el);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    }""", [idx, text])


def _rvb_first_editable(page):
    """返回第一个**可编辑**块的下标（未定位/非文本类块没有「纠正」按钮）"""
    idx = page.evaluate("""() => [...document.querySelectorAll('.rvb-block')]
        .findIndex(el => {
            const b = el.querySelector('.rvb-fix');
            return b && getComputedStyle(b).display !== 'none';
        })""")
    assert idx >= 0, "一个可编辑块都没有 —— 白名单或 span 判据有问题"
    return idx


# ★ **改 P1 既有的 `_rvb_seed`：追加两块「门槛隔离」样本**
#   （Task 6 评审对着真实夹具实测逼出，必须自己造，不能指望夹具里现成有）
#
#   实测：真实夹具切 10 页后是 76 块 = 「66 个 白名单∧有 span」+「10 个 `number`（既非白名单、
#   又无 span）」——**没有任何一块只违反其中一个条件** ⇒ `canEdit` 的两个条件里**单独删掉任一个**
#   （`hasSpan` 或 label 白名单），f13_7 都照样绿：它实际只证明「canEdit 不是恒真」。
#   故补这两块，让两条门槛各自可被否证。**追加在末尾** ⇒ 既有块下标不变、
#   `_rvb_first_editable` 仍返回 0。`bbox` 给 `None`：不画高亮框，免得干扰左栏用例。

```python
    aligned = [b for b in aligned if (b.get("page") or 1) <= _RVB_PAGES]
    aligned.append({"label": "text", "content": "白名单但无 span", "page": 1,
                    "bbox": None, "order": 9998, "global_block_id": 9998,
                    "start_line": None, "end_line": None, "prefix": "", "suffix": "",
                    "clause_no": None})
    # ⚠️ span 必须取 **(1, 2)**，不能取 (0, 1)（Task 8 实现者离线复现 + 控制者复核）：
    # 真实块 0（`## 前言`）的区间**也是** [0, 1) ⇒ 与之重叠 ⇒ `splice` 抛
    # `ValueError: layout 区间重叠：[0, 1) 与游标 1` ⇒ **compose/preview 对该夹具一律 500**
    # （splice 只在区间重叠时抛，路由把它记 ERROR 后回 500）。第 1 行是空行，取 (1, 2) 实测：
    # splice 通过、空 edits 逐字节等价、基线仍 138 条、该样本仍有 span 且非白名单 ⇒ 隔离语义不变。
    aligned.append({"label": "display_formula", "content": "非白名单但有 span", "page": 1,
                    "bbox": None, "order": 9999, "global_block_id": 9999,
                    "start_line": 1, "end_line": 2, "prefix": "", "suffix": "",
                    "clause_no": None})
```


def f13_4_entering_edit_keeps_line_count(page):
    """★ spec R10：进入编辑态前后**行数必须一致**（否则一进一出就多/少一行）

    ★ 同时钉住「**进入编辑态不得抛异常**」。为什么必须单独钉：`$nextTick` 回调里的
    throw 被 Alpine 吞掉，而编辑行**已经 append 进去了** ⇒ 只数行数的断言**看不见**它
    （Task 6 实现者实测：计划原稿把 `isClauseLine` 留到下一个 Task 定义，就会造成
    这种「编辑器坏了但四条用例全绿」的静默失效）。故挂一个 pageerror 收集器。
    """
    task_id = _rvb_seed(page, "4")
    assert task_id is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    # 原行数从**后端下发的 layout** 取（不读页内 Alpine 内部状态，避免断言对象漂移）
    expect = page.evaluate("""async ([tid, i]) => {
        const data = await (await fetch('/import/review/' + tid + '/layout')).json();
        return (data.blocks[i].content || '').split('\\n').length;
    }""", [task_id, idx])
    blk = _rvb_block(page, idx)

    errs = []

    def _on_err(e):
        errs.append(str(e))

    page.on("pageerror", _on_err)
    try:
        _rvb_edit(page, idx)
        got = blk.locator(".rvb-line").count()
    finally:
        page.remove_listener("pageerror", _on_err)
    assert not errs, f"进入编辑态时页面抛了异常（会被 Alpine 吞掉，只有这条断言看得见）：{errs}"
    assert got == expect, f"进出编辑态行数不一致：原文 {expect} 行，编辑态 {got} 行"


def f13_5_edit_and_save_updates_the_block(page):
    """改一行 → 保存 → 块内容变 + 出现「已修改」标"""
    assert _rvb_seed(page, "5") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "PROBE-EDIT-锚点")
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000), \
        "保存后编辑框未收起"
    assert "PROBE-EDIT-锚点" in blk.locator(".rvb-block-bd").inner_text(), "保存后渲染内容没变"
    assert "已修改" in blk.locator(".rvb-block-meta").inner_text(), "保存后没有「已修改」标"


def f13_6_cancel_discards_the_edit(page):
    """取消 ⇒ 内容**一字不动**（不能只收起编辑框）"""
    assert _rvb_seed(page, "6") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    before = blk.locator(".rvb-block-bd").inner_text()
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "不该留下")
    blk.locator("button:has-text('取消')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000)
    assert blk.locator(".rvb-block-bd").inner_text() == before, "取消后内容被改动"


def f13_7_unlocated_and_non_text_blocks_offer_no_fix_button(page):
    """★ `canEdit` 是**两个条件的合取**（有 span ∧ 白名单）⇒ 两条门槛必须**各自**被钉住。

    ⚠️ 为什么不能只断言「存在被锁的块」（Task 6 评审实测）：真实夹具切片里 76 块恰好是
    「66 个 白名单∧有 span」+「10 个 `number`（既非白名单、又无 span）」，**没有一块只违反
    其中一个条件** ⇒ 单独删掉任一条件，原断言照样绿（它实际只证明 canEdit 不是恒真）。
    为此 `_rvb_seed` 补了两块**门槛隔离**样本（见上）。
    """
    assert _rvb_seed(page, "7") is not None, "前置不成立"
    # ⚠️ 一律按**可见性**数：x-show 控制的元素永远在 DOM 里（只数存在性 = 空转恒绿）
    assert page.locator(".rvb-locked:visible").count() > 0, "没有任何块**可见地**标出「不可编辑」"
    assert page.locator(".rvb-fix:visible").count() < page.locator(".rvb-block").count(), \
        "所有块都有「纠正」按钮 —— canEdit 判据没生效"
    # ★ 门槛隔离：两块合成样本的「纠正」都必须不可见
    gate = page.evaluate("""() => {
        const out = {};
        for (const el of document.querySelectorAll('.rvb-block')) {
            const bd = el.querySelector('.rvb-block-bd');
            const t = bd ? bd.textContent.trim() : '';
            if (t === '白名单但无 span' || t === '非白名单但有 span') {
                const fx = el.querySelector('.rvb-fix');
                out[t] = !!(fx && getComputedStyle(fx).display !== 'none');
            }
        }
        return out;
    }""")
    assert '白名单但无 span' in gate and '非白名单但有 span' in gate, \
        f"找不到门槛隔离样本块（`_rvb_seed` 没补上？）：{gate}"
    assert gate['白名单但无 span'] is False, \
        "白名单但**无 span** 的块给了「纠正」⇒ span 门槛被删了（编辑会静默丢）"
    assert gate['非白名单但有 span'] is False, \
        "**非白名单**但有 span 的块给了「纠正」⇒ 白名单门槛被删了"
```

并把这 4 条追加进 `CASES["f13"]`。

- [ ] **Step 6: 跑 f13（7 条）**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: `== f13: 7/7 passed ==`

- [ ] **Step 7: 跑 f12 确认没打破 P1**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f12`
Expected: `== f12: 7/7 passed ==`

- [ ] **Step 8: 类型检查 + 提交**

Run: `pyright app/ocr/layout.py app/routes/import_routes.py scripts/probe_spec_ui.py`
Expected: 0 errors

```bash
git add app/ocr/layout.py app/routes/import_routes.py tests/test_review_layout_routes.py app/templates/partials/ocr_review_blocks.html static/components/review-blocks.js static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 行级编辑器骨架（进入/取消/保存/行增删 + 可编辑白名单下发，探针 f13 7/7）"
```

---

### Task 7: 行内富文本 B/I/S + 整行对齐

**Files:**
- Modify: `app/templates/partials/ocr_review_blocks.html`、`static/components/review-blocks.js`、`static/app.css`、`app/templates/base.html`（版本号）
- Modify: `scripts/probe_spec_ui.py`（f13 追加 4 条）

**Interfaces:**
- Consumes: `window.mdInline`（Task 2）
- 前端新增：`exec(cmd)`、`applyAlign(v)`、`alignOf(el)`、`isClauseLine(el)`、按钮禁用判据 `alignDisabled()`（读**响应式状态** `focusIsClause`）、常量 `ALIGN_LOCK_HINT`
- **对齐作用于当前选中行**（用户 2026-10-05 裁定，D2=A）；**编号行的对齐按钮禁用并给出 title 说明**（Global Constraint 4）

- [ ] **Step 1: 前端实现**

```js
        ALIGN_LOCK_HINT: '编号行必须保持行首为编号，加对齐会改变条文切分',

        exec(cmd) {
            const el = this._focusLine();
            if (!el) return;
            el.focus();
            document.execCommand(cmd, false, null);   // Chrome：产 <b>/<i>/<strike>（不是 <strong>，实测）
            // ⚠️ 标签名不必对齐：`domToMd` 认 b/strong、i/em、s/strike/del；
            //    而我们自己的 `lineToHtml` 产的是 `<strong>/<i>/<s>` ⇒ 重进编辑态看到的是后者
            //    （f13_8 断言的正是重进后的 DOM，故它绿是合理的）。
        },

        // `isClauseLine` **已在 Task 6 定义**（`enterEdit`/`_makeLine` 需要它维护
        // `focusIsClause`）—— 本 Task 只消费，**不要重复定义**。

        /** ⚠️ 判据取**响应式状态**（focusIsClause，由行的 focus/input 事件维护）。
         *  若在这里读 `document.activeElement`，Alpine 不会跟着重算 ⇒ 按钮 disabled 永远是旧值。 */
        alignDisabled() {
            return this.editingIdx < 0 || this.focusIsClause;
        },

        applyAlign(v) {
            const el = this._focusLine();
            if (!el || this.isClauseLine(el)) return;
            el.dataset.align = v || '';
            el.style.textAlign = v || '';
        },

        alignOf(el) { return el.dataset.align || ''; },
```

- [ ] **Step 2: 模板**

`rvb-tools` 内（Save/Cancel 之前）插入：

```html
                                <!-- ⚠️ `@mousedown.prevent` 不是可选项：点按钮会让 contenteditable **失焦**、
                                     选区塌掉，execCommand 就作用不到任何东西（点了 B 没反应）。
                                     prevent 掉 mousedown ⇒ 焦点与选区都留在当前行。 -->
                                <button type="button" title="粗体" @mousedown.prevent @click="exec('bold')"
                                        style="margin:0"><strong>B</strong></button>
                                <button type="button" title="斜体" @mousedown.prevent @click="exec('italic')"
                                        style="margin:0"><em>I</em></button>
                                <button type="button" title="删除线" @mousedown.prevent @click="exec('strikeThrough')"
                                        style="margin:0"><s>S</s></button>
                                <span class="rvb-sep"></span>
                                <!-- 对齐作用于**当前行**（用户裁定）；编号行禁用并说明原因。
                                     禁用元素不弹 tooltip ⇒ title 走**双通道**：外层 span 给可见提示，
                                     按钮自身给程序化读取（探针断言的就是它）—— 本仓既有口径。 -->
                                <span :title="alignDisabled() ? ALIGN_LOCK_HINT : ''">
                                    <button type="button" :title="alignDisabled() ? ALIGN_LOCK_HINT : '左对齐'"
                                            @mousedown.prevent @click="applyAlign(null)"
                                            :disabled="alignDisabled()" style="margin:0">左</button>
                                    <button type="button" :title="alignDisabled() ? ALIGN_LOCK_HINT : '居中'"
                                            @mousedown.prevent @click="applyAlign('center')"
                                            :disabled="alignDisabled()" style="margin:0">中</button>
                                    <button type="button" :title="alignDisabled() ? ALIGN_LOCK_HINT : '右对齐'"
                                            @mousedown.prevent @click="applyAlign('right')"
                                            :disabled="alignDisabled()" style="margin:0">右</button>
                                </span>
                                <span class="rvb-sep"></span>
                                <button type="button" @mousedown.prevent @click="_insertLineAfter(_focusLine())"
                                        style="margin:0">+ 行</button>
                                <button type="button" @mousedown.prevent @click="_deleteLine(_focusLine())"
                                        style="margin:0">- 行</button>
```

`app.css` 追加 `.rvb-sep { flex: 0 0 1px; height: 20px; background: var(--pico-muted-border-color); }`；`base.html` 版本号再递增（`review-blocks.js?v=4`、`app.css?v=41`）。

- [ ] **Step 3: 探针 4 条**

```python
# `_rvb_type_line` / `_rvb_block` / `_rvb_edit` / `_rvb_first_editable` 已在 Task 6 Step 5 定义

def f13_8_bold_survives_save_and_reopen(page):
    """★ 行内富文本往返：选中 → B → 保存 ⇒ md 里出现 **…**；再进编辑仍显示 <strong>"""
    assert _rvb_seed(page, "8") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "PROBE 加粗 x")
    blk.locator("button[title='粗体']").click()
    md = page.evaluate("""(i) => window.mdInline.domToMd(
        document.querySelectorAll('.rvb-block')[i].querySelector('.rvb-line'))""", idx)
    assert "**" in md, f"点 B 之后没有产生粗体标记：{md!r}"
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000)
    _rvb_edit(page, idx)
    assert blk.locator(".rvb-line strong").count() >= 1, "重进编辑后粗体丢失（往返不闭环）"


def f13_9_alignment_wraps_line_and_does_not_add_lines(page):
    """★ 对齐：非编号行可加居中；产物是对齐包裹，且**行数不变**（M9：绝不引入换行）"""
    assert _rvb_seed(page, "9") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    n_before = blk.locator(".rvb-line").count()
    _rvb_type_line(page, idx, "PROBE 图题居中")     # 非编号行 ⇒ 对齐按钮可用
    blk.locator("button:has-text('中')").click()
    line = page.evaluate("""(i) => {
        const el = document.querySelectorAll('.rvb-block')[i].querySelector('.rvb-line');
        return window.mdInline.wrap(window.mdInline.domToMd(el), el.dataset.align || null);
    }""", idx)
    assert "text-align:center" in line, f"对齐未落到行上：{line!r}"
    assert "\n" not in line, "对齐包裹新增了换行 —— 会改变切分"
    assert blk.locator(".rvb-line").count() == n_before, "对齐改变了编辑行数"


def f13_10_alignment_is_disabled_on_clause_number_lines(page):
    """★ 编号行上的对齐必须**禁用**（Global Constraint 4）"""
    assert _rvb_seed(page, "10") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "3.0.9 编号行不得被包裹")   # 内含补发 input，见其 docstring
    blk.locator(".rvb-line").first.click()
    btn = blk.locator("button:has-text('中')")
    # 禁用状态由**响应式状态** `focusIsClause` 决定，而它由 focus/input 事件维护 ⇒
    # 既要等 Alpine 重算完（poll），也要保证 `_rvb_type_line` 真的触发了 input（否则恒红）
    assert poll_until(page, lambda: btn.is_disabled(), 5000), "编号行上对齐按钮应禁用"
    assert "编号行" in (btn.get_attribute("title") or ""), "禁用原因必须给出说明（title）"


def f13_11_enter_splits_into_a_new_line_not_a_br(page):
    """★ Enter ⇒ **新增一个编辑行**（真实换行），绝不产生行内 <br>（spec §8.2 的建模范式）"""
    assert _rvb_seed(page, "11") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    n = blk.locator(".rvb-line").count()
    _rvb_type_line(page, idx, "第一行")
    blk.locator(".rvb-line").first.press("Enter")
    assert poll_until(page, lambda: blk.locator(".rvb-line").count() == n + 1, 5000), \
        "Enter 没有新增编辑行"
    assert blk.locator(".rvb-line br").count() == 0, "Enter 产生了行内 <br>（会与字面 \\n 混淆）"
```

并把这 4 条追加进 `CASES["f13"]`。

- [ ] **Step 4: 跑 f13（11 条）**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: `== f13: 11/11 passed ==`

- [ ] **Step 5: 类型检查 + 提交**

Run: `pyright scripts/probe_spec_ui.py`
Expected: 0 errors

```bash
git add app/templates/partials/ocr_review_blocks.html static/components/review-blocks.js static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 行内 B/I/S 与整行对齐（编号行禁用，探针 f13 11/11）"
```

---

### Task 8: 切分预览面板

**Files:**
- Modify: `app/templates/partials/ocr_review_blocks.html`、`static/components/review-blocks.js`、`static/app.css`、`app/templates/base.html`（版本号）
- Modify: `scripts/probe_spec_ui.py`（f13 追加 3 条）

**Interfaces:**
- Consumes: `POST …/preview`（Task 4）
- 前端新增：`refreshPreview()`（防抖 400 ms）、`baseline`（首次响应的条文序列）、`preview = {count, clauses, added, removed}`、`openClause(clause_no)`（滚到对应块）
- **基线取自「第一次调用」**：`init()` 时以 `edits={}` 调一次 ⇒ 那就是导入前的切分基线；差值由前端算（列表按 `clause_no` 比）

- [ ] **Step 1: 前端实现**

状态声明（加进 `reviewBlocks()` 返回的对象；`baseline` 为 `null` 表示尚未取到）：

```js
        baseline: null,
        preview: { count: 0, clauses: [], added: [], removed: [] },
        previewBusy: false,
        previewErr: '',
        _previewTimer: null,
```

```js
        /** 首次调用就是**基线**（edits 为空 ⇒ 与导入前的切分一致） */
        async refreshPreview() {
            // ⚠️ 只判 `editingIdx`：`submitting` 是 **Task 9** 才引入的状态，本 Task 不该引用它
            //    （读未定义属性不会抛，但那是「成员定义在后面的 Task」这类缺陷的同一个形状，
            //    Task 6 的 `isClauseLine` 已经栽过一次）—— Task 9 接进来时自己补 `|| this.submitting`。
            if (this.editingIdx >= 0) return;
            this.previewBusy = true;
            this.previewErr = '';
            try {
                const resp = await fetch(`/import/review/${this.taskId}/preview`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({edits: this.edits}),
                });
                if (!resp.ok) throw new Error((await resp.json()).detail || resp.status);
                const data = await resp.json();
                if (!this.baseline) this.baseline = data.clauses.map((c) => c.clause_no);
                const now = data.clauses.map((c) => c.clause_no);
                const baseSet = new Set(this.baseline);
                const nowSet = new Set(now);
                this.preview = {
                    count: data.count,
                    clauses: data.clauses,
                    added: now.filter((n) => !baseSet.has(n)),
                    removed: this.baseline.filter((n) => !nowSet.has(n)),
                };
            } catch (e) {
                this.previewErr = '切分预览失败：' + e;
            } finally {
                this.previewBusy = false;
            }
        },

        /** 变更后 400 ms 防抖（实测最重产物 109 ms ⇒ 无需 spec §11.4 的降级方案） */
        schedulePreview() {
            clearTimeout(this._previewTimer);
            this._previewTimer = setTimeout(() => this.refreshPreview(), 400);
        },

        /** 点预览里的某条 ⇒ 滚到它的块（复用既有 focusBlock） */
        openClause(no) {
            const i = this.blocks.findIndex((b) => b.clause_no === no);
            if (i >= 0) this.focusBlock(i);
        },
```

接线：`init()` 末尾 `this.refreshPreview()`；`saveEdit()` 末尾与 `cancelEdit()` 之后改为 `this.schedulePreview()`。

模板（右栏 `.rvb-right` 内、块列表之后）：

```html
            <!-- 切分预览（spec §11.4）：「你这次编辑改了切分吗」—— 本次改版最有价值的一项 -->
            <section class="rvb-preview" x-show="!error" x-cloak>
                <header class="rvb-preview-hd">
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
                    <template x-for="c in preview.clauses" :key="'pc' + c.clause_no">
                        <li :class="preview.added.includes(c.clause_no) && 'rvb-clause-new'"
                            @click="openClause(c.clause_no)">
                            <span class="rvb-cn" x-text="c.clause_no"></span>
                            <span x-text="c.title"></span>
                        </li>
                    </template>
                </ol>
            </section>
```

`app.css`：

```css
.rvb-preview { border-top: 1px solid var(--pico-muted-border-color); max-height: 220px; display: flex; flex-direction: column; }
.rvb-preview-hd { display: flex; gap: 0.75rem; align-items: center; padding: 0.4rem 0.6rem; font-size: 0.85rem; }
.rvb-added { color: #2f7d32; } .rvb-removed { color: #c62828; }
.rvb-clause-list { overflow: auto; margin: 0; padding: 0 0.6rem 0.4rem 1.6rem; font-size: 0.85rem; }
.rvb-clause-list li { cursor: pointer; }
.rvb-clause-new { background: #e8f5e9; }
.rvb-cn { color: var(--pico-muted-color); margin-right: 0.4rem; }
```

`base.html` 版本号递增（`review-blocks.js?v=5`、`app.css?v=42`）。

- [ ] **Step 2: 探针 3 条**

```python
def f13_12_preview_lists_clauses_of_the_document(page):
    """预览面板列出当前切分；条数与块列表无关（它是整篇的切分）"""
    assert _rvb_seed(page, "12") is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000), \
        "切分预览没有任何条文"
    n = page.locator(".rvb-clause-list li").count()
    assert n > 50, f"夹具切片条数异常：{n}"


def _rvb_clause_block_idx(page, task_id):
    """找一个「**编辑后真能改变切分条数**」的可编辑块（用与端点同源的 preview **现地试选**）。

    ⚠️ **不能按内容正则静态挑**（Task 8 实现者离线复现 + 本机实测，本计划原稿的静态判据在此
    夹具上失效）：
      - 原稿的 `^(#{1,6}\\s|\\d+(\\.\\d+)*…)` **首个命中是目录行** `1 总则 ..... 1`（下标 14）
        —— 它不是真条文 ⇒ 追加编号行 **+0**、抹掉编号 **+0**，两条用例都不动；
      - 版式受解析器的「标题吸收」等怪癖支配（下标 24 的 `1.0.1` 抹掉编号是 **+0 而非 −1**，
        因为 `## 1 总 则` 被并进来补位）——「看着像编号行」远不足以预测条数变化，
        而要静态预测就得**把解析器重写一遍**（本仓反复警告的「第二把尺子」）。
    故改为：拿本块真的造两种编辑、问 preview 结果，挑同时满足「追加 ⇒ +1」与「抹掉首行编号 ⇒ −1」
    的第一个块（本夹具实证为下标 25 `1.0.2`）。

    **这不构成循环论证**：本 helper 只做**前置挑选**；端点若坏（非 200/条数对不上），
    它找不到块而**以「找不到」变红**，而不是把失败藏起来。
    """
    idx = page.evaluate("""async ([tid]) => {
        const L = await (await fetch('/import/review/' + tid + '/layout')).json();
        const editable = new Set(L.editable_labels || []);
        const prev = async (edits) => {
            const r = await fetch('/import/review/' + tid + '/preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({edits})});
            if (!r.ok) throw new Error('preview 返回 ' + r.status);
            return (await r.json()).count;
        };
        const base = await prev({});
        for (let i = 0; i < L.blocks.length; i++) {
            const b = L.blocks[i];
            if (b.start_line === null || b.start_line === undefined) continue;
            if (!editable.has(b.label)) continue;
            const ls = (b.content || '').split('\\n');
            if (!/^\\d+(\\.\\d+)*\\s*[.、．]?\\s*\\S/.test((ls[0] || '').trim())) continue;
            const plus = await prev({[i]: ls.concat(['9.9.9 PROBE 新增条文']).join('\\n')});
            if (plus !== base + 1) continue;
            const minus = await prev({[i]: ['PROBE 编号被抹掉'].concat(ls.slice(1)).join('\\n')});
            if (minus === base - 1) return i;
        }
        return -1;
    }""", [task_id])
    assert idx >= 0, "找不到「追加编号行 +1 且抹掉编号 −1」的可编辑块（夹具或端点有问题）"
    return idx


def _rvb_append_line(page, idx, text):
    """在编辑中块的**首行末尾**按 Enter 新增一行，并**真实键入** text（忠实模拟用户操作）。

    ⚠️ 不要拿 `_rvb_type_line` 的「替换首行」来实现「新增一条」：那是**替换**语义，
    换文本不改条数（实测 138→138）。新增必须真按 Enter（组件的 `_onLineKey` 会新建编辑行），
    再用 `.type()` 键入 —— 键入会真触发 `input`，不像程序化赋值。
    """
    blk = _rvb_block(page, idx)
    first = blk.locator(".rvb-line").first
    first.click()
    first.press("End")
    first.press("Enter")
    assert poll_until(page, lambda: blk.locator(".rvb-line").count() >= 2, 3000), \
        "Enter 没有新增编辑行（_onLineKey 失效？）"
    blk.locator(".rvb-line").nth(1).type(text)


def f13_13_editing_a_clause_line_changes_the_preview(page):
    """★ 预览的价值所在：在编号行后补一条**带编号的新行** ⇒ 预览条数 +1 且标出新增"""
    task_id = _rvb_seed(page, "13")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000)
    base = page.locator(".rvb-clause-list li").count()
    idx = _rvb_clause_block_idx(page, task_id)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_append_line(page, idx, "9.9.9 PROBE 新增条文")
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() == base + 1, 15000), \
        f"补写编号行后预览条数未 +1（{base} → {page.locator('.rvb-clause-list li').count()}）"
    assert page.locator(".rvb-clause-new").count() >= 1, "新增条文未被标出"


def f13_14_swallowing_a_clause_line_shows_as_removed(page):
    """★ 反向：把编号行改成普通文本 ⇒ 该条**消失**并标红（「把相邻条吞掉」这个失效）"""
    task_id = _rvb_seed(page, "14")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000)
    base = page.locator(".rvb-clause-list li").count()
    idx = _rvb_clause_block_idx(page, task_id)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "PROBE 编号被抹掉")   # **替换**首行 ⇒ 编号没了 ⇒ 少一条
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() < base, 15000), \
        f"抹掉编号后预览条数未减少（仍为 {base}）"
    assert "消失" in page.locator(".rvb-preview-hd").inner_text(), "没有给出「消失 N 条」的提示"
```

并追加进 `CASES["f13"]`。

- [ ] **Step 3: 跑 f13（14 条）**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: `== f13: 14/14 passed ==`

- [ ] **Step 4: 提交**

```bash
git add app/templates/partials/ocr_review_blocks.html static/components/review-blocks.js static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 切分预览面板（与提交同源；新增/消失条文可比对，探针 f13 14/14）"
```

---

### Task 9: 提交链路（compose → confirm → 轮询）

**Files:**
- Modify: `app/templates/partials/ocr_review_blocks.html`、`static/components/review-blocks.js`、`static/app.css`、`app/templates/base.html`（版本号）
- Modify: `scripts/probe_spec_ui.py`（f13 追加 3 条）

**Interfaces:**
- Consumes: `POST …/compose`（Task 3）、既有 `POST …/confirm`（raw body，**零改动**）、既有 `GET /import/progress/{id}`
- 前端新增：`confirmImport()`（照抄 `ocr_review.html::confirmImport` 的 raw body + `htmx.process` 范式）、`submitError`、`submitting`

- [ ] **Step 1: 前端实现**

状态声明（加进 `reviewBlocks()` 返回的对象）：

```js
        submitting: false,
        submitError: '',
```

```js
        /** 提交：compose（拼装）→ confirm（既有 raw body 通道）⇒ confirm 端点零改动 */
        async confirmImport() {
            if (this.submitting) return;
            if (this.editingIdx >= 0) {
                this.submitError = '请先「保存」或「取消」正在编辑的块，再提交。';
                return;
            }
            this.submitting = true;
            this.submitError = '';
            const box = document.getElementById('rvb-result');
            const scroller = this._resultScroller();
            if (scroller) scroller.scrollTop = scroller.scrollHeight;   // 瞬时，避免「点了没反应」误判
            try {
                const cr = await fetch(`/import/review/${this.taskId}/compose`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({edits: this.edits}),
                });
                if (!cr.ok) throw new Error((await cr.json()).detail || ('compose ' + cr.status));
                const md = (await cr.json()).md_text;
                // 与原 md 逐字节相同 ⇒ 用户没改任何东西，给一次确认机会（防误点）
                const resp = await fetch(`/import/review/${this.taskId}/confirm`, {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain; charset=utf-8'},
                    body: md,
                });
                const html = await resp.text();
                if (box) box.innerHTML = html;
                if (!resp.ok) {
                    this.submitError = (html || '').replace(/<[^>]+>/g, '').trim()
                        || ('提交失败（HTTP ' + resp.status + '）');
                } else if (window.htmx && box) {
                    window.htmx.process(box);      // 进度片段带 hx-get/hx-trigger，必须让 htmx 接管
                }
            } catch (e) {
                this.submitError = '提交失败：' + e;
            } finally {
                this.submitting = false;
            }
        },

        _resultScroller() {
            const box = document.getElementById('rvb-result');
            for (let s = box && box.parentElement; s; s = s.parentElement) {
                if (s.scrollHeight > s.clientHeight) return s;
            }
            return null;
        },
```

模板（`.rvb-root` 内、两栏之后）：

```html
    <!-- 提交 / 取消（与源码审查页同构：raw body + 结果区 + htmx 轮询） -->
    <div class="rvb-submit">
        <small style="color:var(--pico-muted-color)">审查完毕后点击确认继续导入流程（解析 + 分类 + 索引）</small>
        <span style="position:relative;display:inline-flex;align-items:center">
            <span x-show="submitting" x-cloak class="rvb-mask"></span>
            <button type="button" style="position:relative;z-index:1;margin:0" :disabled="submitting"
                    @click="confirmImport()" x-text="submitting ? '提交中…' : '✅ 确认并继续导入'"></button>
        </span>
        <button type="button" class="secondary" style="margin:0"
                hx-post="/import/review/{{ task_id }}/cancel"
                hx-confirm="确定取消本次导入？已上传的文件和识别结果将被删除。">✖ 取消导入</button>
    </div>
    <div id="rvb-result" class="rvb-result-slot">
        <p x-show="submitError" x-text="submitError" style="color:#c00"></p>
    </div>
```

`app.css`：

```css
.rvb-submit { display: flex; gap: 0.75rem; align-items: center; justify-content: space-between;
              border-top: 1px solid var(--pico-muted-border-color); padding-top: 0.75rem; margin-top: 0.75rem; }
.rvb-mask { position: absolute; inset: 0; background: rgba(255,255,255,.55);
            border: 1px dashed var(--pico-muted-border-color); border-radius: 6px; z-index: 2; }
.rvb-result-slot { min-height: 96px; }
```

`base.html` 版本号递增（`review-blocks.js?v=6`、`app.css?v=43`）。

- [ ] **Step 2: 探针 3 条**

```python
def _rvb_wait_done(page, task_id, timeout_ms=180000):
    """轮询隔离实例的进度 JSON，直到任务离开 processing（真实提交会跑 Phase 2）"""
    import json as _json
    import time as _time
    t0 = _time.time()
    while (_time.time() - t0) * 1000 < timeout_ms:
        txt = page.evaluate("""async (tid) => (await fetch('/import/progress/' + tid + '/json')).text()""",
                            task_id)
        try:
            st = _json.loads(txt).get("status")
        except Exception:
            st = None
        if st in ("done", "error"):
            return st
        page.wait_for_timeout(1000)
    return "timeout"


def f13_15_editing_a_block_does_not_let_submit_while_editing(page):
    """★ 编辑态下提交必须**被拦**并给出可执行的提示（否则用户的改动被判丢）"""
    assert _rvb_seed(page, "15") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    _rvb_edit(page, idx)
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: "请先" in page.locator("#rvb-result").inner_text(), 5000), \
        "编辑态下点提交没有被拦（会静默丢掉未保存的改动）"


def f13_16_submit_sends_the_edited_text_to_phase2(page):
    """★ 端到端：改一行 → 提交 → 任务进入 done，且**库里条文含改后的文本**。

    这是「编辑后导入结果与手改 md 等价」的**端到端证据**：走的正是 compose → confirm → Phase 2
    这条真实链路，不是桩。
    """
    task_id = _rvb_seed(page, "16")
    assert task_id is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "9.9.8 PROBE 端到端条文")
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000)
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: page.locator("#rvb-result").inner_text().strip() != "", 10000), \
        "提交后结果区没有回显（说明 confirm 没被调到）"
    st = _rvb_wait_done(page, task_id)
    assert st == "done", f"端到端导入未成功：{st}"
    # 库里真的落进了编辑后的文本 ⇒ 编辑没有停在浏览器里
    assert _rvb_imported_has_text("探针块审查 16", "PROBE 端到端条文"), \
        "编辑后的文本没有入库（compose/confirm 链路断了）"


def f13_17_submit_without_edits_still_imports(page):
    """未编辑直接提交 ⇒ 也能正常导入（规范化不会把内容改坏到切不出条）"""
    task_id = _rvb_seed(page, "17")
    assert task_id is not None, "前置不成立"
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: page.locator("#rvb-result").inner_text().strip() != "", 10000)
    st = _rvb_wait_done(page, task_id)
    assert st == "done", f"未编辑提交未成功：{st}"


def _rvb_imported_spec_id(title_fragment):
    """定位本次导入的规范 id。

    种子带唯一后缀（code `PROBE-RVB 16` / title `探针块审查 16`），但导入侧会对
    code/title 做归一化（`_parse_filename_to_code_title` 的剥噪声）⇒ **两列都试**，
    再退回「最新一条」（探针刚导完，最新那条就是它；探针运行期间没有别的写入者）。
    """
    from app.database import get_db
    frag = f"%{title_fragment}%"
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM specifications WHERE title LIKE ? OR code LIKE ? "
            "ORDER BY id DESC LIMIT 1", (frag, frag)).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM specifications ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _rvb_imported_clause_count(title_fragment):
    """数这个任务**真正入库**的条文数。

    直查隔离库而不是查接口：要断言的正是「预览条数 == 入库条数」，
    中间再隔一层接口（它有自己的过滤）会把差异盖掉。
    """
    from app.database import get_db
    sid = _rvb_imported_spec_id(title_fragment)
    if sid is None:
        return 0
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM clauses WHERE spec_id=?", (sid,)).fetchone()[0]


def _rvb_imported_has_text(title_fragment, needle):
    """库里该规范的任一条文含 needle 即真 —— **content 或 title 都要查**。

    ⚠️ **只查 `content` 会让一条完全正常的链路永久变红**（Task 9 实现者实测 + 控制者复核）：

    ```text
    "9.9.8 PROBE 端到端条文"                       → 0 条！（裸编号行无自身正文 ⇒ 内节点不入库）
    "9.9.8 PROBE 端到端条文\n\n后续正文一句。"      → 1 条，title='PROBE 端到端条文'，content='后续正文一句。'
    夹具那三条单行（3.0.1/3.0.2/3.0.3 各一行）      → 3 条，title=''，content='第一条。'
    ```

    即：编号后的那段文字**可能落在 `title` 而不是 `content`**（取决于是否有后续正文与 R14 兄弟表决）。
    故两个字段都要查 —— 这不是放宽断言：变异（跳过 compose 用原文提交）**仍会让 f13_16 变红**
    （原文里根本没有 needle，两个字段都查不到）。
    """
    from app.database import get_db
    sid = _rvb_imported_spec_id(title_fragment)
    if sid is None:
        return False
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE spec_id=? AND (content LIKE ? OR title LIKE ?)",
            (sid, f"%{needle}%", f"%{needle}%")).fetchone()[0]
    return n > 0


def f13_18_preview_count_equals_imported_clause_count(page):
    """★★ spec §18 的 P2 出口判据：**切分预览与导入后实际条文数一致**。

    这条是整个 P2 的验收铰链 —— 预览若不准，用户拿它当凭据就是被误导。
    ⚠️ 若首跑不相等，**先查 Phase 2 是否有本条未镜像的过滤**（`_filter_cover_clauses`
    之外的任何一步），把同一条规则补进 preview，**不得为了让它变绿而放宽断言**。
    """
    task_id = _rvb_seed(page, "18")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000), \
        "切分预览为空，无法对账"
    preview_n = page.locator(".rvb-clause-list li").count()
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: page.locator("#rvb-result").inner_text().strip() != "", 10000)
    assert _rvb_wait_done(page, task_id) == "done", "端到端导入未成功"
    imported_n = _rvb_imported_clause_count("探针块审查 18")
    assert imported_n > 0, "库里一条都没入库 —— 断言会退化成 0 == 0 的空转"
    assert preview_n == imported_n, \
        f"切分预览与入库条数不一致：预览 {preview_n} / 入库 {imported_n}"
```

并追加进 `CASES["f13"]`。

> ⚠️ 这三条端到端用例（16/17/18）会**真实走完 Phase 2**（写隔离库与隔离向量目录）。跑完按 §运行方式清理 `data/_probe_*`。

- [ ] **Step 3: 跑 f13（18 条）**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: `== f13: 18/18 passed ==`

- [ ] **Step 4: 跑全量后端回归 + 类型检查**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Expected: **全绿**（P1 基线 1426 passed，本计划新增约 25 条）。其中 **P0 的地基用例必须保持绿**：
`tests/test_layout_anchor.py::test_splice_with_no_edits_is_byte_identical`（不编辑 ⇒ **逐字节相等**）
—— 规范化只加在 `splice` **之后**（`normalize_md`），绝不掺进 `splice` 本身；这正是 spec §10.3
「步 1 与步 2 必须分开」的落地形态。

Run: `pyright app tests scripts`
Expected: 0 errors

- [ ] **Step 5: 提交**

```bash
git add app/templates/partials/ocr_review_blocks.html static/components/review-blocks.js static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 审查页提交链路（compose 拼装后走既有 raw body confirm，探针 f13 18/18）"
```

---

### Task 10: 非空网验证（变异测试）+ 收尾

**Files:**
- Modify: `scripts/probe_spec_ui.py`（仅新增用例，不改产品代码）

**目的（spec §15.3）**：证明这网是**非空网** —— 关键能力被摘掉时必须**变红**。变异测试的判据只在**形式层**（本仓 memory：语义等价档只能否证形式层声明）。

- [ ] **Step 1: 变异 1 —— 摘掉规范化**

把 `confirm_review` 里的 `content = normalize_md(content)` 临时注释掉，跑：
Run: `D:/Python/python.exe -m pytest tests/test_confirm_transport.py -v`
Expected: **`test_confirm_normalizes_tables_and_literal_n` 变红**（其余仍绿）
改回。

- [ ] **Step 2: 变异 2 —— 忽略 edits**

把 `_compose_or_error` 里的 `splice(md_text, blocks, edits)` 临时改成 `splice(md_text, blocks, {})`，跑：
Run: `D:/Python/python.exe -m pytest tests/test_review_compose.py tests/test_review_preview.py -v`
Expected: **编辑相关用例变红**（`applies_only_the_edited_range` / `reflects_an_edit_that_adds_a_clause`）
改回。

- [ ] **Step 3: 变异 3 —— 对齐落到编号行上**

把 `review-blocks.js` 的 `alignDisabled()` 临时改为 `return false;`，跑：
Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: **`f13_10_alignment_is_disabled_on_clause_number_lines` 变红**
改回。

- [ ] **Step 4: 三处改回后复跑**

Run: `D:/Python/python.exe -m pytest tests/test_confirm_transport.py tests/test_review_compose.py tests/test_review_preview.py -q`
Run: `D:/Python/python.exe scripts/probe_spec_ui.py f13`
Expected: 全绿 / `18/18`

- [ ] **Step 5: 复现 P1/P0 基线（回归不倒退）**

Run: `D:/Python/python.exe -m pytest tests/ -q`
Run: `D:/Python/python.exe scripts/probe_spec_ui.py f12`
Run: `pyright app tests scripts`
Expected: 全绿 · `f12: 7/7` · 0 errors

- [ ] **Step 6: 提交**

```bash
git add scripts/probe_spec_ui.py
git commit -m "test: P2 非空网验证（三处变异各自变红，基线未倒退）"
```

---

## 已知边界（实施时**不得**悄悄绕过，验证时逐条对照）

| # | 边界 | 依据 |
| --- | --- | --- |
| B1 | **未定位块不可编辑**（夹具实测 2 个：`table` idx 286 / `chart` idx 522）。`splice` 只能按行区间回写，允许编辑就必然**静默丢改动** —— 比拒绝更坏 | M1 + `splice` 契约 |
| B2 | **表格 / 公式 / 图片块本期不可编辑** | spec §18：表格 P3、图片公式 P4 |
| B3 | **对齐会让包裹行内的 markdown 不解析**（`<span>` 内的 `**x**` 由 DOMPurify 保留原样）—— 已用 M9 的形式把损失降到「只在包裹行内」，且**图题无强调**，实际影响为零 | M9 |
| B4 | **图题被再居中会嵌套**：`figure_title` 的居中 div 记在 `prefix/suffix`（P0），用户再点「中」会产生 `div > span` 双层包裹。视觉无差别，存储略冗余 —— 本期**不特判** | P0 的 `prefix/suffix` 设计 |
| B5 | **粘贴只取纯文本**；多行按行拆分。剪贴板里的图片/富文本格式一律丢弃 | Global Constraint 2（不引依赖） |
| B6 | 行内 **不做 HTML 转义** ⇒ 用户手打的 `<b>` 会被渲染成粗体（与源码审查页同一风险面，DOMPurify 兜底） | 逐字节可逆不变式（探针 f13_2） |
| B7 | 「**复制**」按钮（spec §8.1 的 Paddle 范式里与「纠正」并列）**本期不做** | spec §18 P2 未列；避免撑大本期 |
| B8 | `layout.json` 只在 PaddleOCR-VL + 扫描件路径产生 ⇒ **存量产物演示不了 compose** | M7 |
| B9 | **整行只有一个 `<br>` 视为空行**（Chrome 会在空的可编辑行留占位 `<br>`）。代价：一行**恰好只有字面 `\n`** 的退化作废行会被读成空行 —— 该行在提交时本来也会被 `normalize_md` 转成 `<br>`，无实际损失 | `lineToHtml` / `domToMd` 的空行表示 |
| B10 | 「**纠正**」按钮**常驻可见**（spec §8.1 写的是「悬停/聚焦时浮出」）。常驻更易发现，也让探针能按**可见性**断言（悬停浮出会让 f13_7 的可见性判据失效）。若要改回悬停浮出，**必须同步改 f13_7** | spec §8.1 vs 探针可验证性 |

## 不做（明确排除）

- 块级增删 / 合并 / 拆分（spec §2.2；新块在原 PDF 上没有 bbox）
- 表格插删行列 / 合并拆分 / 单元格内嵌图片删除（P3）
- 图片替换 / 删除 / 裁剪（P4）、公式就地编辑与弹窗（P4）
- 搜索/替换跨块改造（P4）
- 字面 `\n` 的**存量**清洗（会改派生文本 ⇒ 必须连带重建索引，spec §10.4 / R6）
- 段落等级 `Tt`（H1–H6）：spec §2.2 已裁定不做
