# P3 表格编辑器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OCR 审查页能**就地编辑表格** —— 插入/删除行列、合并/拆分单元格、删除单元格内嵌图片、单元格文字对齐，并保证未编辑的块逐字节不变。

**Architecture:** 后端在**导入期**把表格块在 md 里的原文存进 `layout.json` 的新字段 `md_html`（请求期不重算，与 T1 接线同口径）；前端新增纯函数模块 `table-editor.js`（`parse` / `serialize` / `validate` + 结构操作），由 `review-blocks.js` 的 `enterEdit`/`saveEdit` 分支调用；写回复用既有 `compose → splice` 通道 ⇒ **后端零新增端点**。**DOM 即模型**：`<table>` 元素本身是唯一真源，不另建数据结构。

**Tech Stack:** FastAPI + Jinja2 + Alpine.js 3（无构建步骤）+ Pico CSS；pytest；Playwright 探针（`scripts/probe_spec_ui.py`）。

**Spec:** `docs/superpowers/specs/2026-10-05-ocr-review-table-editor-design.md`（本计划逐条实现它；设计依据与实测证据都在那份 spec 里）。上游 spec：`2026-10-04-ocr-review-block-editor-design.md`（§8.3 / §8.6 / §18 / §19）。

---

## Global Constraints

来源：项目 `CLAUDE.md`、spec、以及本仓已固化的教训（每条都对应一次已发生的事故）。

- **TDD**：先写失败用例再写实现。每个 Task 提交前必须跑**本次变更关联的增量测试**，并跑 **`pyright app tests scripts`**（**必须是 CLI `pyright`**；`D:/Python/python.exe -m pyright` 会报 `No module named pyright`），**不得新增 error**（基线 0 errors 0 warnings）。提交信息 `type: 描述`，**单 Task 单提交**。
- **永不 `git add .`**：逐个文件 `git add <路径>`。
- **静态资源版本号**：改了 `static/` 下的 js/css（或模板引用），**必须递增** `app/templates/base.html` 里对应的 `?v=N`。当前值：`app.css?v=50`、`review-blocks.js?v=11`、`md-inline.js?v=1`。新增 `table-editor.js` 时以 `?v=1` 入列。
- **探针运行方式**：`D:/Python/python.exe scripts/probe_spec_ui.py f14`。四个隔离路径 —— `DATABASE_PATH` / `UPLOAD_DIR` / `OUTPUT_DIR` / `LANCE_DB_PATH` —— **必须同时对 uvicorn 服务端与探针进程导出**；端口 **8123**、**不加 `--reload`**；**每轮跑前重新 `cp data/spec_query.db data/_probe_spec.db`**；跑完**杀 8123 并删四个隔离产物**。跑法全文在 `scripts/probe_spec_ui.py` 的文件头 docstring。
- **探针证明力受限**：runner **无逐例 try/except**，一例失败即中止整组 ⇒ 一例红只能证明「它之前仍绿」，**不得**写成「其余全绿」。
- **JS 无单测基建**（本仓没有 package.json / JS 测试文件）⇒ **JS 行为一律用探针断言**；**纯函数**用 `page.evaluate` 在页内直接单元式断言（不必走 UI），这是本计划给 JS 侧定下的验证方式。
- **`layout.json` 的锚定基准是库里 `import_tasks.md_text`**，**不是**磁盘的 `data/outputs/{tid}/{tid}.md`（实测两者 10239 vs 10449 行）。任何校验 span / 行号 / `md_html` 的脚本都必须读库里那份，先用 `base_sha256` 确认。
- **不得改**：`app/parser/md_parser.py`、`app/parser/text_clean.py`、`md-inline.js` 的既有语义、`splice` / `align_clauses` 的行为。
- 不主动安装依赖；不改系统配置。

---

## File Structure

| 文件 | 职责 | 本计划的改动 |
| --- | --- | --- |
| `app/ocr/layout.py` | 锚定 / 拼接 / 落盘 | `anchor_blocks` 给表格块挂 `md_html`；`EDITABLE_TEXT_LABELS` 加 `table` |
| `static/components/table-editor.js` | **新文件**：表格的解析 / 序列化 / 校验 / 结构操作（纯函数，`window.tableEditor`） | 全部 |
| `static/components/review-blocks.js` | 审查页 Alpine 组件 | `enterEdit`/`saveEdit` 加表格分支；工具条接线；表内 B/I/S |
| `app/templates/partials/ocr_review_blocks.html` | 审查页模板 | 表格工具条 + 表内元素容器 |
| `static/app.css` | 样式 | 表格编辑器的选中态 / 单元格 / 工具条 |
| `app/templates/base.html` | 静态资源清单 | 引入 `table-editor.js?v=1`；递增版本号 |
| `scripts/probe_spec_ui.py` | 探针套件 | `_rvb_seed` 加 opt-in `with_table`；新增 **f14** 组 |
| `TODOS.md` | 遗留登记 | 新增 T42（表格编辑器遗留 / 已知行为） |

---

## 阶段一：地基（后端数据 + JS 纯函数模块）

### Task 1: 表格块带上 `md_html` 并变为可编辑

**Files:**
- Modify: `app/ocr/layout.py`（`anchor_blocks` 与 `EDITABLE_TEXT_LABELS`）
- Test: `tests/test_layout_anchor.py`（文件末尾追加）

**Interfaces:**
- Consumes: `anchor_blocks(base_text, blocks, ignore_labels)` 既有签名与返回值
- Produces: **label 为 `table` 且已锚定的块**，其 dict 多一个键 `md_html: str`（== 该块 span 对应的 md 原文，`"\n".join(lines[start_line:end_line])`）；其它 label **不带该键**。`EDITABLE_TEXT_LABELS` 含 `"table"`。

- [ ] **Step 1: 写失败用例（三条）**

追加到 `tests/test_layout_anchor.py` 末尾：

```python
# ══ 表格块携带 md 原文（P3 表格编辑器的数据前提） ═══════════

def test_table_block_carries_md_html():
    """★ 表格块必须带上它在 md 里的**原文** —— 编辑与写回都以它为准。

    为什么不能拿块的 `content` 当编辑源：`content` 是 OCR 的**去样式版**，而
    `border=1` / `style='margin: auto'` 是表格边框与居中的**唯一来源**（渲染侧无补偿 CSS）。
    """
    base = "正文一。\n\n" + _T_MD_1 + "\n\n正文二。"
    bl = [_blk("正文一。", 0), _blk(_T_BLK, 1, label="table"), _blk("正文二。", 2)]
    anchored, _ = anchor_blocks(base, bl)
    tb = anchored[1]
    assert tb["start_line"] is not None, "表格块没锚上（前置不成立）"
    assert "md_html" in tb, "表格块没带 md_html"
    assert tb["md_html"] == _T_MD_1, "md_html 不等于 md 里那段原文"


def test_non_table_block_has_no_md_html():
    """★ md_html **只给表格块** —— 别的 label 带上它会让 payload 无谓膨胀。"""
    base = "正文一。\n\n正文二。"
    anchored, _ = anchor_blocks(base, [_blk("正文一。", 0), _blk("正文二。", 1)])
    assert all("md_html" not in b for b in anchored)


def test_unanchored_table_has_no_md_html():
    """★ 没锚上的表格块**不得**凭空带 md_html（没有 span 就没有"它在 md 里的原文"）。"""
    base = "正文里没有表格。"
    anchored, _ = anchor_blocks(base, [_blk("正文里没有表格。", 0), _blk(_T_BLK, 1, label="table")])
    assert anchored[1]["start_line"] is None
    assert "md_html" not in anchored[1]
```

- [ ] **Step 2: 跑用例确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py -q -k "md_html"`
Expected: `test_table_block_carries_md_html` FAIL（`AssertionError: 表格块没带 md_html`）；另两条可能已 PASS（它们是「不许乱带」的哨兵）。

- [ ] **Step 3: 实现（`anchor_blocks` 挂字段 + 白名单）**

在 `app/ocr/layout.py` 的 `anchor_blocks` 里，把成功锚定那一段（当前是）：

```python
        s, e, prefix, suffix = cands[0]
        cursor = e
        anchored += 1
        out.append({**blk, "start_line": s, "end_line": e,
                    "prefix": prefix, "suffix": suffix})
```

改为：

```python
        s, e, prefix, suffix = cands[0]
        cursor = e
        anchored += 1
        # 表格块的 md 原文必须**随块下发**（P3 表格编辑器的编辑与写回都以它为准）：
        # 块的 `content` 是 OCR 的去样式版，而 `border/style` 是边框与居中的唯一来源。
        # 其它 label 的 content 与 md 行本就等价（锚定判据就是按行比对的），无需重复携带。
        extra = {"md_html": "\n".join(lines[s:e])} if blk.get("label") == "table" else {}
        out.append({**blk, "start_line": s, "end_line": e,
                    "prefix": prefix, "suffix": suffix, **extra})
```

并把 `EDITABLE_TEXT_LABELS`（约 `:52`）改为：

```python
EDITABLE_TEXT_LABELS = frozenset({
    "doc_title", "paragraph_title", "content", "text", "figure_title", "vision_footnote",
    # P3：表格块走**自己的编辑器**（不是行级），但仍须在白名单里 —— `canEdit` 的白名单
    # 是"这块能不能改"的唯一判据，`enterEdit` 再按 label 分派到行编辑器或表格编辑器。
    "table",
})
```

- [ ] **Step 4: 跑用例确认通过 + 邻近回归**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py tests/test_ocr_layout.py tests/test_review_layout_routes.py -q`
Expected: 全绿（`test_ocr_layout.py` 里 `finalize_layout` 的用例会跟着验证 `md_html` 能落盘 —— 它是普通 dict 键，`save_layout` 原样写出）。

- [ ] **Step 5: 真语料门禁（读**库里**的 md，不是磁盘文件）**

```bash
export PYTHONUTF8=1
D:/Python/python.exe - <<'PYEOF'
import json, pathlib, sys
sys.path.insert(0, '.')
from app.database import get_db
from app.ocr.layout import anchor_blocks, base_sha256

for tid in ("d66c2e0b",):
    lay = json.loads(pathlib.Path(f"data/outputs/{tid}/layout.json").read_text(encoding="utf-8"))
    with get_db() as c:
        md = c.execute("SELECT md_text FROM import_tasks WHERE task_id=?", (tid,)).fetchone()["md_text"]
    assert base_sha256(md) == lay["base_sha256"], "读错源：md 与 layout 不是同一次锚定"
    anchored, st = anchor_blocks(md, lay["blocks"])
    tabs = [b for b in anchored if b["label"] == "table"]
    ok = [b for b in tabs if b.get("md_html")]
    print(f"表格块 {len(tabs)}，带 md_html 的 {len(ok)}")
    for b in ok[:2]:
        print("  首 60 字:", b["md_html"][:60])
PYEOF
```

Expected: `表格块 24，带 md_html 的 24`（`d66c2e0b` 实测口径）。

- [ ] **Step 6: 回填存量**

```bash
cp data/outputs/d66c2e0b/layout.json data/outputs/d66c2e0b/layout.json.bak
D:/Python/python.exe scripts/backfill_layout.py            # dry-run 先看
D:/Python/python.exe scripts/backfill_layout.py --apply
```

Expected: 只有 `d66c2e0b` 被写回（其余目录无 `md_text` ⇒ 跳过，属预期）。

- [ ] **Step 7: 提交**

```bash
git add app/ocr/layout.py tests/test_layout_anchor.py
git commit -m "feat: 表格块携带 md 原文（md_html）并进入可编辑白名单"
```

---

### Task 2: `table-editor.js` 纯函数模块（解析 / 序列化 / 校验）

**Files:**
- Create: `static/components/table-editor.js`
- Modify: `app/templates/base.html`（引入脚本）
- Test: `scripts/probe_spec_ui.py`（新增 **f14** 组第一例，用 `page.evaluate` 单元式断言）

**Interfaces:**
- Produces: `window.tableEditor = { parse, serialize, validate }`
  - `parse(html: string) -> HTMLTableElement | null`
  - `serialize(tableEl: HTMLTableElement) -> string`（保留表自身属性、剥掉编辑器痕迹）
  - `validate(tableEl: HTMLTableElement) -> { ok: boolean, reason: string }`

- [ ] **Step 1: 先给探针加 f14 组与第一例（失败的单元式断言）**

在 `scripts/probe_spec_ui.py` 中新增（放在 `f13_24` 之后）：

```python
# ═══════════════════════════════════════════
# f14：P3 表格编辑器
# ═══════════════════════════════════════════
# 本组**不走 UI**，而是用 `page.evaluate` 直接对 `window.tableEditor` 做单元式断言 ——
# 本仓没有 JS 单测基建（无 package.json），这是给 JS 纯函数定下的验证方式。
# 每例自带前置：先断言模块已被页面加载，否则整组会以"函数未定义"这种误导性的方式红。

_TBL_HTML = ("<table border=1 style='margin: auto; word-wrap: break-word;'>"
             "<tr><td style='text-align: center;'>序号</td>"
             "<td style='text-align: center;'>图 形</td>"
             "<td style='text-align: center;'>说 明</td></tr>\n"
             "<tr><td style='text-align: center;'>1</td>\n"
             "<td style='text-align: center;'><img src=\"imgs/t.jpg\" /></td>\n"
             "<td style='text-align: center;'>紧急避难设施</td></tr></table>")


def f14_1_round_trip_is_byte_identical(page):
    """★ 地基：**未做任何编辑 ⇒ 序列化结果与原文逐字节相同**。

    ⚠️ 这条**需要"源串缓存"才能成立**：浏览器解析**必然归一化**（插 `<tbody>`、属性引号统一为
    双引号、丢弃自闭合 `/>`），纯 DOM→serialize **做不到**字节相等。缓存（`parse` 记源串 +
    归一化基线，`serialize` 比对后决定返回哪一个）是"未改动 ⇒ 原样返回"的唯一途径（spec §2.5）。
    夹具 `_TBL_HTML` 用的是**真实产物的写法**（单引号、无 tbody、`<img ... />`），故本用例
    不是对拍出来的空转。
    """
    page.goto(f"{BASE}/")                       # 任意应用页即可（base.html 已加载模块）
    has = page.evaluate("() => typeof window.tableEditor === 'object'")
    assert has, "table-editor.js 没被页面加载（检查 base.html 的 script 引用）"
    got = page.evaluate("""(html) => window.tableEditor.serialize(window.tableEditor.parse(html))""",
                        _TBL_HTML)
    assert got == _TBL_HTML, f"往返不逐字节：\\n原文 {_TBL_HTML[:80]}\\n得到 {got[:80]}"


def f14_2_serialize_strips_editor_artifacts(page):
    """★ 序列化必须剥掉**编辑器痕迹**（contenteditable / 选中态 class / 高亮包裹），
    否则它们会跟着写进 md。"""
    got = page.evaluate("""(html) => {
        const t = window.tableEditor.parse(html);
        t.setAttribute('contenteditable', 'true');
        t.querySelector('td').classList.add('rvb-cell-sel');
        t.querySelector('td').setAttribute('contenteditable', 'true');
        return window.tableEditor.serialize(t);
    }""", _TBL_HTML)
    assert 'contenteditable' not in got, f"编辑器痕迹漏进序列化：{got[:120]}"
    assert 'rvb-cell-sel' not in got, f"选中态 class 漏进序列化：{got[:120]}"


def f14_3_validate_rejects_malformed_tables(page):
    """★ 校验的三条判据**逐条**都要挡住：越界 `rowspan` / 各行宽度不等 / 空洞。

    ⚠️ 一条用例覆盖三种非法输入是**有意的**（不新增用例，避免打乱后续 Task 的编号）：
    `validate` 的三条判据是本模块的**已声明契约**，声明了却没人验证＝假承诺。
    合法放行由 `f14_4` 反向钉住。
    """
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const out = {};
        // ① rowspan 越界
        let t = T.parse(html);
        t.querySelector('td').setAttribute('rowspan', '99');
        out.rowspan = T.validate(t);
        // ② 各行宽度不等（删掉第 2 行的最后一格）
        t = T.parse(html);
        const cells = t.querySelectorAll('tr')[1].querySelectorAll('td');
        cells[cells.length - 1].remove();
        out.ragged = T.validate(t);
        // ③ 空洞（给首格设 rowspan=2，再删掉它正下方那一行的对应格）
        t = T.parse(html);
        t.querySelector('tr td').setAttribute('rowspan', '2');
        t.querySelectorAll('tr')[1].querySelector('td').remove();
        out.hole = T.validate(t);
        return out;
    }""", _TBL_HTML)
    for name, v in got.items():
        assert v["ok"] is False and v["reason"], f"{name} 这种非法结构没被拦住：{v}"


def f14_4_validate_accepts_well_formed_table(page):
    """★ 反向：合法表必须放行（否则校验会把所有保存都挡掉）。"""
    got = page.evaluate("""(html) => window.tableEditor.validate(window.tableEditor.parse(html))""",
                        _TBL_HTML)
    assert got["ok"] is True, f"合法表被误拒：{got}"


CASES["f14"] = [f14_1_round_trip_is_byte_identical,
                f14_2_serialize_strips_editor_artifacts,
                f14_3_validate_rejects_malformed_tables,
                f14_4_validate_accepts_well_formed_table]
```

- [ ] **Step 2: 跑到红**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f14`（按 Global Constraints 起隔离实例）
Expected: `f14_1` 红，报「table-editor.js 没被页面加载」。

- [ ] **Step 3: 实现模块**

创建 `static/components/table-editor.js`：

```javascript
/* 表格编辑器（P3）：解析 / 序列化 / 结构校验 / 结构操作。
 *
 * **DOM 即模型**：`<table>` 元素本身是唯一真源，不另建数据结构 —— 两套账必然分叉。
 * 本文件只做**纯函数级**的事（给定元素 → 新元素 / 字符串 / 判定），不碰 Alpine 状态。
 * 由 `review-blocks.js` 在编辑态里调用（与 `md-inline.js` 同一模式）。
 *
 * 关键约束（见 spec `2026-10-05-ocr-review-table-editor-design.md` §2.5）：
 *   - `serialize` 必须**保留表自身的属性**（`border=1` / `style='margin: auto; …'` 是
 *     边框与居中的唯一来源，渲染侧无补偿 CSS）；
 *   - `serialize` 必须**剥掉编辑器痕迹**（`contenteditable`、选中态 class 等），
 *     否则它们会跟着写进 md。
 */
(function () {
    // 编辑器自己加的痕迹：序列化前一律清除
    const EDITOR_ATTRS = ['contenteditable'];
    const EDITOR_CLASSES = ['rvb-cell-sel'];

    function parse(html) {
        const src = String(html || '');
        const box = document.createElement('div');
        box.innerHTML = src;
        const el = box.querySelector('table');
        if (el) {
            // ⚠️ 必须缓存**原始串**：浏览器解析必然归一化（插 `<tbody>`、属性引号统一为双引号、
            //    丢弃自闭合 `/>`）⇒ DOM 往返**做不到**字节相等。要兑现 spec §2.5 的
            //    「未改动 ⇒ 逐字节不变」，只能在 parse 时留一份源串、serialize 时比对基线。
            // 真实产物实测（`d66c2e0b` 24 份 md_html）：单引号、无 tbody —— 与夹具逐字一致。
            el._rvbBaseline = el.outerHTML;
            // ⚠️ `_rvbBaseline` 是**表级**、`_rvbSource` 必须是**输入级且恰好覆盖这张表** ——
            //    两者作用域一旦不一致，未改动时 `serialize` 会把**整个入参串**当块内容返回
            //    （**静默写坏 md**，而且"往返一致"型断言对它恒绿、抓不到）。
            //    故只在「入参串的唯一元素子节点就是这张表」时才缓存源串。
            //    用 `children`（仅元素）而非 `childNodes`，可容忍首尾空白文本节点。
            if (box.children.length === 1 && box.children[0] === el) {
                el._rvbSource = src;
            }
        }
        return el;
    }

    function _scrub(el) {
        el.removeAttribute('contenteditable');
        for (const a of EDITOR_ATTRS) el.removeAttribute(a);
        for (const c of EDITOR_CLASSES) el.classList.remove(c);
        el.querySelectorAll('*').forEach((n) => {
            for (const a of EDITOR_ATTRS) n.removeAttribute(a);
            for (const c of EDITOR_CLASSES) n.classList.remove(c);
        });
        return el;
    }

    function serialize(tableEl) {
        if (!tableEl) return '';
        const scrubbed = _scrub(tableEl.cloneNode(true));
        // 与 parse 时的**归一化基线**比较：相等 ⇒ 用户没动过 ⇒ 返回**原始串**（逐字节不变）。
        // 不相等 ⇒ 归一化形态（语义等价，属性值保留）。见 spec §2.5「源串缓存」。
        if (tableEl._rvbSource && tableEl._rvbBaseline
            && scrubbed.outerHTML === tableEl._rvbBaseline) {
            return tableEl._rvbSource;
        }
        return scrubbed.outerHTML;
    }

    /** 占位矩阵：`m[r][c]` = 占据该格的元素（或 null）。越界 / 重叠 ⇒ 返回原因字符串。 */
    function _grid(tableEl) {
        const rows = [...tableEl.querySelectorAll('tr')];
        const m = [];
        for (let r = 0; r < rows.length; r++) m.push([]);
        for (let r = 0; r < rows.length; r++) {
            let c = 0;
            for (const cell of [...rows[r].children]) {
                if (!/^(TD|TH)$/i.test(cell.tagName)) continue;
                const rs = parseInt(cell.getAttribute('rowspan') || '1', 10);
                const cs = parseInt(cell.getAttribute('colspan') || '1', 10);
                if (!(rs >= 1) || !(cs >= 1)) return { err: `第 ${r + 1} 行有非法 span（${rs}/${cs}）` };
                if (r + rs > rows.length) return { err: `第 ${r + 1} 行 rowspan=${rs} 越出表边界` };
                while (m[r][c]) c++;
                for (let i = 0; i < rs; i++) {
                    for (let j = 0; j < cs; j++) {
                        const rr = r + i, cc = c + j;
                        while (m[rr] && m[rr][cc]) { if (j === 0 && i === 0) c = ++cc; else return { err: `第 ${rr + 1} 行出现重叠格` }; }
                        if (!m[rr]) return { err: `第 ${rr + 1} 行不存在` };
                        m[rr][cc] = cell;
                    }
                }
                c += cs;
            }
        }
        return { grid: m, rows };
    }

    function validate(tableEl) {
        const g = _grid(tableEl);
        if (g.err) return { ok: false, reason: g.err };
        const widths = g.grid.map((row) => row.filter(Boolean).length);
        const w = widths[0] || 0;
        if (w === 0) return { ok: false, reason: '表格没有单元格' };
        if (widths.some((x) => x !== w)) return { ok: false, reason: `各行宽度不一致（${widths.join('/')}）` };
        if (g.grid.some((row) => row.some((x) => !x))) return { ok: false, reason: '表格存在空洞' };
        return { ok: true, reason: '' };
    }

    window.tableEditor = { parse, serialize, validate };
})();
```

在 `app/templates/base.html` 的 `md-inline.js` 那一行**之后**加：

```html
    <script src="/static/components/table-editor.js?v=1"></script>
```

- [ ] **Step 4: 跑到绿**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f14`
Expected: `== f14: 4/4 passed ==`

- [ ] **Step 5: 提交**

```bash
git add static/components/table-editor.js app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 新增表格编辑器纯函数模块（解析/序列化/校验）+ f14 探针组"
```

---

## 阶段二：接入编辑态（第一个用户可见里程碑）

### Task 3: 表格块走表格编辑器（`enterEdit` / `saveEdit` 分支）

**Files:**
- Modify: `static/components/review-blocks.js`、`app/templates/partials/ocr_review_blocks.html`、`app/templates/base.html`
- Test: `scripts/probe_spec_ui.py`（`_rvb_seed` 加 opt-in `with_table`；f14 追加用例）

**Interfaces:**
- Consumes: Task 1 的 `b.md_html`、Task 2 的 `window.tableEditor`
- Produces: `enterEdit(idx)` 在 `b.label === 'table'` 时于 `.rvb-lines` 内建出**可编辑表格**（容器 `.rvb-table-edit`，内含 `<table>`）；`saveEdit()` 在该情形下把 `tableEditor.serialize(tableEl)` 写进 `edits[__i]`。`_rvb_seed(page, suffix, extra_md="", with_table=False)`。

- [ ] **Step 1: 给探针夹具加 opt-in 合成表 + 追加用例**

在 `scripts/probe_spec_ui.py` 的 `_rvb_seed` 签名改为：

```python
def _rvb_seed(page, suffix, extra_md="", with_table=False):
    """造一个**带 layout.json** 的待审查任务并打开审查页；前置不成立返回 None。

    `extra_md` / `with_table` 均为 **opt-in**：默认与原先逐字一致（不影响 f12/f13 的夹具计数）。
    `with_table=True` 时追加一张**合成表格**（md 原文 + 对应块），供 f14 使用 ——
    真实夹具里唯一的 table 块在第 29 页，而本探针只切到 `_RVB_PAGES` 页，会被过滤掉。
    """
```

在函数体末尾（两个 `aligned.append(...)` 之后、`task_id = _make_review_task(` 之前）插入：

```python
    if with_table:
        md = md + _RVBT_MD
        _lines = md.split("\n")
        try:
            _ts = next(i for i, l in enumerate(_lines)
                       if l.lstrip().startswith("<table") and "探针表格" in l)
        except StopIteration:
            return None
        # gid 取 10000（比上面两个合成块大）⇒ 阅读顺序排在最后，与 md 行号递增一致
        aligned.append({"label": "table", "content": _RVBT_CONTENT, "page": 1,
                        "bbox": None, "order": 10000, "global_block_id": 10000,
                        "start_line": _ts, "end_line": _ts + 1, "prefix": "", "suffix": "",
                        "clause_no": None, "md_html": _lines[_ts]})
```

并在文件靠上位置（`_rvb_seed` 之前）定义两个常量：

```python
# f14 专用合成表：**md 侧带样式、块侧去样式**（复刻真实产物的两种序列化），
# 且**含一张内嵌图片** —— 出口判据「行列被当成图片塞进合并格」要用到它。
_RVBT_MD = ("\n\n<table border=1 style='margin: auto; word-wrap: break-word;'>"
            "<tr><td style='text-align: center;'>序号</td>"
            "<td style='text-align: center;'>图 形</td>"
            "<td style='text-align: center;'>说 明</td></tr>"
            "<tr><td style='text-align: center;'>1</td>"
            "<td style='text-align: center;'><img src=\"imgs/t.jpg\" /></td>"
            "<td style='text-align: center;'>探针表格</td></tr></table>")
_RVBT_CONTENT = ('<table><tr><td>序号</td><td>图 形</td><td>说 明</td></tr>'
                 '<tr><td>1</td><td><img src="imgs/t.jpg" /></td>'
                 '<td>探针表格</td></tr></table>')
```

追加用例：

```python
def f14_5_entering_edit_on_a_table_shows_an_editable_table(page):
    """★ 表格块点「纠正」⇒ 块内出现**可编辑表格**，且单元格是可编辑的。

    先钉前置：夹具里**确有**一个带 span 的 table 块 —— 否则本组会以"没有表格块"这种
    误导性的方式红（真实夹具的 table 块在第 29 页、会被页数切片过滤掉，故必须走 with_table）。
    """
    task_id = _rvb_seed(page, "14a", with_table=True)
    assert task_id is not None, "前置不成立（合成表没进去）"
    tbl_blocks = page.evaluate(
        "() => Alpine.$data(document.querySelector('[x-data]')).blocks"
        ".filter(b => b.label === 'table' && b.start_line !== null).length")
    assert tbl_blocks >= 1, f"夹具没有可编辑的表格块（{tbl_blocks}）"
    fix = page.locator('[data-block-idx]', has_text="探针表格")
    fix.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000), \
        "点纠正后没有出现可编辑表格"
    cells = page.locator(".rvb-table-edit table td")
    assert cells.count() == 6, f"单元格数不对：{cells.count()}"
    assert cells.first.get_attribute("contenteditable") == "true", "单元格不可编辑"


def f14_6_saving_an_edited_table_writes_the_md_region(page):
    """★ 保存 ⇒ 该表在 md 里的那段被**整段替换**成序列化结果（写回路径打通）。

    断言到**结果**：保存后块的 `renderBlock` 里出现改后的文字，且**开标签属性仍在**
    （`border=1` / `margin: auto` —— 丢了就等于把表格的边框与居中弄没了）。
    """
    task_id = _rvb_seed(page, "14b", with_table=True)
    assert task_id is not None, "前置不成立"
    blk = page.locator('[data-block-idx]', has_text="探针表格")
    blk.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000)
    cell = page.locator(".rvb-table-edit table td").nth(5)          # 「探针表格」那格
    cell.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("改过的说明")
    page.locator("button:has-text('保存')").first.click()
    assert poll_until(page, lambda: "改过的说明" in blk.inner_text(), 5000), "保存后块内容没变"
    md_html = page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('[x-data]'));
        return d.edits[d.blocks.findIndex(b => b.label === 'table' && b.start_line !== null)];
    }""")
    assert md_html and "改过的说明" in md_html, f"edits 里没有改后的表：{str(md_html)[:80]}"
    # ⚠️ 断言必须按**属性值**核，**不能**按源串字面核 `"border=1"` —— 表格一经改动，
    #    序列化走归一化形态（`border=1` → `border="1"`、引号统一、自动插 `<tbody>`），
    #    字面断言会**假红**。（本用例改了格内文字 ⇒ 一定走归一化分支。）
    probe = page.evaluate("""(html) => {
        const t = window.tableEditor.parse(html);
        return { border: t.getAttribute('border'), style: t.getAttribute('style') || '' };
    }""", md_html)
    assert probe["border"] == "1", f"border 属性丢了（表格会没边框）：{probe}"
    assert "margin: auto" in probe["style"] or "margin:auto" in probe["style"], \
        f"margin:auto 丢了（表格会左对齐）：{probe}"
    assert "contenteditable" not in md_html, "编辑器痕迹漏进了 md"


def f14_7_save_refuses_a_broken_table_and_keeps_editing(page):
    """★ 校验**在保存路径上**也要挡住（不是只在纯函数层挡）：非法结构 ⇒ 不写入 edits、
    留在编辑态、块内出现可读报错。这是 spec §2.4「绝不写坏 md」的可观测形式。"""
    task_id = _rvb_seed(page, "14c", with_table=True)
    assert task_id is not None, "前置不成立"
    blk = page.locator('[data-block-idx]', has_text="探针表格")
    blk.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000)
    page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('[x-data]'));
        d._tableEl().querySelector('td').setAttribute('rowspan', '99');
    }""")
    blk.locator("button:has-text('保存')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-err").is_visible(), 3000), \
        "非法结构没有给出块内报错"
    state = page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('[x-data]'));
        return { editing: d.editingIdx, edits: Object.keys(d.edits).length };
    }""")
    assert state["editing"] >= 0, "校验失败后不该退出编辑态（会丢失用户改动）"
    assert state["edits"] == 0, f"非法结构竟然写进了 edits：{state}"
```

并把这三例加进 `CASES["f14"]` 列表。

- [ ] **Step 2: 跑到红**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f14`
Expected: `f14_5` 红，报「点纠正后没有出现可编辑表格」。

- [ ] **Step 3: 实现（模板 + JS 分支）**

`app/templates/partials/ocr_review_blocks.html`：在编辑区那一行 `.rvb-lines` **之前**加一个容器（表格编辑器专用，避免与行编辑器的选择器互相污染）：

```html
                            <!-- 表格编辑器容器：仅当该块 label === 'table' 时由 JS 填充。
                                 与行编辑器的 `.rvb-lines` 分开，避免 `.rvb-line` 选择器互相干扰。 -->
                            <div class="rvb-table-edit" x-show="editingIdx === i && blocks[i].label === 'table'"></div>
                            <!-- 表格编辑态的**块内**错误提示（spec §2.4：校验失败要保留编辑态、
                                 只在块内报错）。**不要**复用页面级的 `submitError` —— 那个横幅
                                 在提交条下方（已落到折线以下），用户当下看不到。 -->
                            <p class="rvb-table-err" x-show="editingIdx === i && tableError"
                               x-text="tableError" style="color:#c00;margin:0.25rem 0 0"></p>
```

`static/components/review-blocks.js`：

`enterEdit` 改为（在 `$nextTick` 回调里按 label 分派）：

```js
        enterEdit(idx) {
            const b = this.blocks[idx];
            if (!b || !this.canEdit(b)) return;
            this.editingIdx = idx;
            this.$nextTick(() => {
                const box = this._editBox();
                box.innerHTML = '';
                if (b.label === 'table') {
                    // ⚠️ **必须用 `md_html`，且缺了就不进编辑态** —— 绝不能回落到 `b.content`：
                    //    它是 OCR 的**去样式版**，保存时会把它写回 md ⇒ 表格丢掉 `border=1` 与
                    //    `margin: auto`（边框与居中的唯一来源，渲染侧无补偿 CSS）。宁可不让改。
                    if (!b.md_html) return;
                    const t = window.tableEditor.parse(b.md_html);
                    if (!t) return;
                    t.querySelectorAll('td,th').forEach((c) => c.setAttribute('contenteditable', 'true'));
                    this._tableSnapshots = [t.outerHTML];       // 快照栈的起点（Task 5 起用）
                    const host = this._tableHost();
                    if (host) { host.innerHTML = ''; host.appendChild(t); }
                    return;
                }
                for (const ln of window.mdInline.splitLines(this.contentOf(b))) {
                    box.appendChild(this._makeLine(ln));
                }
                const first = box.querySelector('.rvb-line');
                this.focusIdx = 0;
                this.focusIsClause = first ? this.isClauseLine(first) : false;
                if (first) first.focus();
            });
        },
```

并新增取容器与取表格的 helper（放在 `_editBox` 旁边）：

```js
        /** 表格编辑器容器（同样**按块下标**取 —— `x-ref` 在 `x-for` 里会被最后一项覆盖） */
        _tableHost() {
            const list = this.$refs.list;
            return list
                ? list.querySelector(`[data-block-idx="${this.editingIdx}"] .rvb-table-edit`)
                : null;
        },

        /** 当前编辑态里的 `<table>` 元素（表格块专用；非表格块返回 null） */
        _tableEl() {
            const host = this._tableHost();
            return host ? host.querySelector('table') : null;
        },
```

`saveEdit()` 改为：

```js
        saveEdit() {
            const b = this.blocks[this.editingIdx];
            if (b && b.label === 'table') {
                const t = this._tableEl();
                if (!t) return;
                const v = window.tableEditor.validate(t);
                if (!v.ok) { this.tableError = '表格结构不合法：' + v.reason; return; }
                this.edits[b.__i] = window.tableEditor.serialize(t);
                this.editingIdx = -1;
                this.schedulePreview();
                return;
            }
            const box = this._editBox();
            const els = [...box.querySelectorAll('.rvb-line')];
            const md = els.map((el) => window.mdInline.wrap(
                window.mdInline.domToMd(el), el.dataset.align || null)).join('\n');
            this.edits[this.blocks[this.editingIdx].__i] = md;
            this.editingIdx = -1;
            this.schedulePreview();
        },
```

`static/app.css` 加最小样式（后续 Task 再扩）：

```css
/* 表格编辑器（P3）：容器与表格本体。单元格的选中态在 Task 5 加。 */
.rvb-table-edit { border: 1px solid var(--pico-muted-border-color); border-radius: 6px; padding: 0.25rem; }
.rvb-table-edit table { width: auto; max-width: 100%; }
.rvb-table-edit td, .rvb-table-edit th { padding: 2px 6px; }
.rvb-table-edit td:focus, .rvb-table-edit th:focus { outline: 2px solid var(--pico-primary); }
```

同时在组件的 state 里补三个字段（与既有 `submitError` 并列），并在进/出编辑态时清掉表格错误（否则上一次的报错会跟着下一块显示）：

```js
        tableError: '',            // 表格编辑态的**块内**错误提示（不要复用页面级 submitError）
        _tableSnapshots: [],       // 结构操作的快照栈（Task 5 的撤销用它）
        _selAnchor: null,          // 选区锚点格（DOM 元素，不进响应式；Task 5 用）
```

`enterEdit` 开头加 `this.tableError = '';`，`cancelEdit()` 里也加一句 `this.tableError = '';`。

**版本号**：`app/templates/base.html` 里 `app.css?v=50` → `?v=51`、`review-blocks.js?v=11` → `?v=12`。

- [ ] **Step 4: 跑到绿**

Run: `D:/Python/python.exe scripts/probe_spec_ui.py f14`
Expected: `== f14: 7/7 passed ==`

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py tests/test_ocr_review.py -q`
Run: `pyright app tests scripts`
Expected: 全绿 / 0 errors

- [ ] **Step 5: 提交**

```bash
git add static/components/review-blocks.js app/templates/partials/ocr_review_blocks.html static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 表格块在审查页可就地编辑（enterEdit/saveEdit 走表格编辑器）"
```

---

## 阶段三：结构操作

### Task 4: 插入 / 删除行列

**Files:**
- Modify: `static/components/table-editor.js`、`app/templates/partials/ocr_review_blocks.html`、`static/components/review-blocks.js`、`app/templates/base.html`
- Test: `scripts/probe_spec_ui.py`（f14 追加）

**Interfaces:**
- Produces: `tableEditor.insertRow(tableEl, refCell, before) -> boolean`、`insertCol(tableEl, refCell, before)`、`deleteRows(tableEl, cells) -> boolean`、`deleteCols(tableEl, cells)`；`review-blocks.js` 的 `tableOp(name)` 统一入口（含**操作前压快照**）。

- [ ] **Step 1: 追加用例**

```python
def f14_8_insert_and_delete_row_keep_the_rectangle(page):
    """★ 插入/删除行后表仍是**矩形**（校验通过），且**插在选区所在行之前**。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const firstCell = t.querySelector('td');
        T.insertRow(t, firstCell, true);              // 在第 1 行之前插一行
        const rows1 = t.querySelectorAll('tr').length;
        const v1 = T.validate(t);
        T.deleteRows(t, [t.querySelector('td')]);     // 删掉刚插的那行的首格所在行
        return { rows1, ok1: v1.ok, reason1: v1.reason,
                 rows2: t.querySelectorAll('tr').length, ok2: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["rows1"] == 3, f"插入后行数不对：{got}"
    assert got["ok1"] is True, f"插入后结构非法：{got['reason1']}"
    assert got["rows2"] == 2 and got["ok2"] is True, f"删除后不对：{got}"


def f14_9_insert_and_delete_column_keep_the_rectangle(page):
    """★ 列同理：插列后每行宽度 +1，删列后恢复，且始终通过校验。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        T.insertCol(t, t.querySelector('td'), false);   // 在第 1 格**之后**插一列
        const cols1 = t.querySelectorAll('tr')[0].children.length;
        const ok1 = T.validate(t).ok;
        T.deleteCols(t, [t.querySelectorAll('tr')[0].children[1]]);
        return { cols1, ok1, cols2: t.querySelectorAll('tr')[0].children.length,
                 ok2: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["cols1"] == 4 and got["ok1"] is True, f"插列不对：{got}"
    assert got["cols2"] == 3 and got["ok2"] is True, f"删列不对：{got}"
```

（加进 `CASES["f14"]`。）

- [ ] **Step 2: 跑到红** —— `D:/Python/python.exe scripts/probe_spec_ui.py f14`，Expected: `f14_8` 红（`insertRow is not a function`）。

- [ ] **Step 3: 实现**

`table-editor.js` 追加（`window.tableEditor` 里注册）：

```javascript
    /** 在 `refCell` 所在行**之前/之后**插一行；新行为**空格**（列数取该行宽度）。 */
    function insertRow(tableEl, refCell, before) {
        const tr = refCell.closest('tr');
        if (!tr) return false;
        const width = [...tr.children].reduce(
            (n, c) => n + parseInt(c.getAttribute('colspan') || '1', 10), 0);
        const fresh = document.createElement('tr');
        for (let i = 0; i < width; i++) fresh.appendChild(document.createElement('td'));
        tr.parentNode.insertBefore(fresh, before ? tr : tr.nextSibling);
        return true;
    }

    /** 在 `refCell` 所在列之前/之后插一列：**逐行**在对应下标处插入空格。
     *  ⚠️ 合并格（colspan>1）会让"列下标"与"格下标"不相等 ⇒ 用占位矩阵定位，
     *     落在合并格范围内时**拒绝**（返回 false，不猜）。 */
    function insertCol(tableEl, refCell, before) {
        const g = _grid(tableEl);
        if (g.err) return false;
        let tr = refCell.closest('tr');
        const r = [...tableEl.querySelectorAll('tr')].indexOf(tr);
        const c = g.grid[r].indexOf(refCell);
        if (c < 0) return false;
        const at = before ? c : c + 1;
        if (g.grid.some((row) => row[at] && row[at] !== row[at - 1])) {
            // 目标列位置落在某个跨列格内部 ⇒ 拒绝
            return false;
        }
        tableEl.querySelectorAll('tr').forEach((row, ri) => {
            const cell = g.grid[ri] && g.grid[ri][at];
            // 同一合并格在矩阵里出现多次，只在**该格起始列**处插一次
            if (cell && g.grid[ri].indexOf(cell) !== at) return;
            const td = document.createElement('td');
            const ref = at === 0 ? row.firstChild
                                 : (g.grid[ri][at - 1] || row.lastChild);
            if (cell === ref) row.insertBefore(td, cell);
            else if (ref) row.insertBefore(td, ref.nextSibling);
            else row.appendChild(td);
        });
        return true;
    }

    /** 删除 `cells` 覆盖到的**全部**行（含只被覆盖一部分的）。 */
    function deleteRows(tableEl, cells) {
        const rows = [...tableEl.querySelectorAll('tr')];
        const doomed = new Set(cells.map((c) => c.closest('tr')));
        if (doomed.size >= rows.length) return false;      // 不许把表删空
        doomed.forEach((tr) => tr.remove());
        return true;
    }

    /** 删除 `cells` 覆盖到的**全部**列；整列都没了 ⇒ 返回 false（不许删空）。 */
    function deleteCols(tableEl, cells) {
        const g = _grid(tableEl);
        if (g.err) return false;
        const rows = [...tableEl.querySelectorAll('tr')];
        const cols = new Set();
        for (const c of cells) {
            const r = rows.indexOf(c.closest('tr'));
            g.grid[r].forEach((cell, ci) => { if (cell === c) cols.add(ci); });
        }
        const width = g.grid[0].length;
        if (cols.size >= width) return false;
        rows.forEach((row, ri) => {
            [...cols].sort((a, b) => b - a).forEach((ci) => {
                const cell = g.grid[ri][ci];
                if (!cell) return;
                const start = g.grid[ri].indexOf(cell);
                if (start !== ci) return;                  // 合并格只在起始列处理一次
                const cs = parseInt(cell.getAttribute('colspan') || '1', 10);
                if (cs > 1) cell.setAttribute('colspan', String(cs - 1));
                else cell.remove();
            });
        });
        return true;
    }
```

并在 `window.tableEditor = { parse, serialize, validate }` 里补上这四个函数名。

模板加工具条（仅在表格编辑态显示；**尺寸同行编辑器**）：

```html
                            <!-- 表格工具条（仅表格块编辑态）。按钮尺寸与行编辑器一致：
                                 `.rvb-tools button` 已是 0.72rem / padding 1px 6px，故复用该类。 -->
                            <div class="rvb-tools" x-show="editingIdx === i && blocks[i].label === 'table'">
                                <button type="button" @click="tableOp('insertRowBefore')" style="margin:0"
                                        title="在选中格所在行之前插入一行">+ 行</button>
                                <button type="button" @click="tableOp('insertRowAfter')" style="margin:0"
                                        title="在选中格所在行之后插入一行">行 +</button>
                                <button type="button" @click="tableOp('insertColBefore')" style="margin:0"
                                        title="在选中格所在列之前插入一列">+ 列</button>
                                <button type="button" @click="tableOp('insertColAfter')" style="margin:0"
                                        title="在选中格所在列之后插入一列">列 +</button>
                                <span class="rvb-sep"></span>
                                <button type="button" @click="tableOp('deleteRow')" style="margin:0"
                                        title="删除选中格所在的整行">- 行</button>
                                <button type="button" @click="tableOp('deleteCol')" style="margin:0"
                                        title="删除选中格所在的整列">- 列</button>
                            </div>
```

`review-blocks.js` 加统一入口（**操作前压快照**；选中格用 Task 5 引入的 `selCell`，本 Task 先只支持单格）：

```js
        /** 表格结构操作的统一入口：**压快照 → 执行 → 复验**。
         *
         * ⚠️ 快照必须在**操作前**压：这是「撤销」按钮唯一的数据来源（spec §2.4）。
         * ⚠️ 每次结构操作后立刻 `validate`：非法就**整体回滚到刚压的快照**，
         *    而不是把用户留在半坏的表里（校验器只会在保存时再挡一次，那时已经晚了）。
         */
        tableOp(name) {
            const t = this._tableEl();
            const cell = (t && t.querySelector('.rvb-cell-sel')) || (t && t.querySelector('td,th'));
            if (!t || !cell) return;
            const snap = t.outerHTML;
            let ok = false;
            switch (name) {
                case 'insertRowBefore': ok = window.tableEditor.insertRow(t, cell, true); break;
                case 'insertRowAfter': ok = window.tableEditor.insertRow(t, cell, false); break;
                case 'insertColBefore': ok = window.tableEditor.insertCol(t, cell, true); break;
                case 'insertColAfter': ok = window.tableEditor.insertCol(t, cell, false); break;
                case 'deleteRow': ok = window.tableEditor.deleteRows(t, [cell]); break;
                case 'deleteCol': ok = window.tableEditor.deleteCols(t, [cell]); break;
            }
            const v = window.tableEditor.validate(t);
            if (!ok || !v.ok) {
                this._restoreTable(snap);
                this.tableError = ok ? ('表格结构不合法：' + v.reason) : '该操作在当前选区不可用';
                return;
            }
            this.tableError = '';
            this._tableSnapshots.push(snap);
        },

        /** 用快照整体还原表格（结构操作回滚 / 撤销共用）。
         *  Task 4 只做「还原」；Task 5 会在末尾补一句 `this._selectTable()` 重新绑定选中格。 */
        _restoreTable(snap) {
            const host = this._tableHost();
            const t = window.tableEditor.parse(snap);
            if (!host || !t) return;
            t.querySelectorAll('td,th').forEach((c) => c.setAttribute('contenteditable', 'true'));
            host.innerHTML = '';
            host.appendChild(t);
        },
```

**版本号**：`app.css?v=51` → `?v=52`、`review-blocks.js?v=12` → `?v=13`、`table-editor.js?v=1` → `?v=2`。

- [ ] **Step 4: 跑到绿** —— `== f14: 9/9 passed ==`；`pyright` 0 errors
- [ ] **Step 5: 提交**

```bash
git add static/components/table-editor.js app/templates/partials/ocr_review_blocks.html static/components/review-blocks.js app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 表格插入/删除行列（含操作后校验与整体回滚）"
```

---

### Task 5: 选择模型 · 合并 / 拆分 · 撤销

**Files:**
- Modify: `static/components/table-editor.js`、`static/components/review-blocks.js`、`app/templates/partials/ocr_review_blocks.html`、`static/app.css`、`app/templates/base.html`
- Test: `scripts/probe_spec_ui.py`（f14 追加）

**Interfaces:**
- Produces: `tableEditor.selectRange(tableEl, anchorCell, focusCell)`（给矩形区域内的格加 `rvb-cell-sel`）、`tableEditor.mergeCells(tableEl, selectedCells) -> boolean`、`tableEditor.splitCell(tableEl, cell) -> boolean`；`review-blocks.js` 的 `_selectTable()`、`tableUndo()`。

- [ ] **Step 1: 追加用例**

```python
def f14_10_merge_sets_spans_and_drops_swallowed_cells(page):
    """★ 合并：左上格设 rowspan/colspan，**其余格删除**，结构仍合法。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const rows = t.querySelectorAll('tr');
        const cells = [...rows[0].children, ...rows[1].children];   // 整表 2×3
        const ok = T.mergeCells(t, cells);
        const tl = rows[0].children[0];
        return { ok, rs: tl.getAttribute('rowspan'), cs: tl.getAttribute('colspan'),
                 childRows: [...t.querySelectorAll('tr')].map(r => r.children.length),
                 valid: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["ok"] is True and got["valid"] is True, f"合并结果不合法：{got}"
    assert got["rs"] == "2" and got["cs"] == "3", f"span 不对：{got}"
    assert got["childRows"] == [1, 0], f"被吞格没删干净：{got}"


def f14_11_split_restores_empty_cells(page):
    """★ 拆分：按 colspan/rowspan 补出**空格**（内容不还原 —— spec §8.3 用户已接受）。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const rows = [...t.querySelectorAll('tr')];
        T.mergeCells(t, [...rows[0].children, ...rows[1].children]);
        const tl = rows[0].children[0];
        const before = tl.getAttribute('colspan');
        const ok = T.splitCell(t, tl);
        return { before, ok, rs: tl.getAttribute('rowspan'), cs: tl.getAttribute('colspan'),
                 rows: [...t.querySelectorAll('tr')].map(r => r.children.length),
                 valid: T.validate(t).ok, firstText: rows[0].children[0].textContent.trim() };
    }""", _TBL_HTML)
    assert got["ok"] is True and got["valid"] is True, f"拆分结果不合法：{got}"
    assert got["rs"] is None and got["cs"] is None, f"span 属性没清掉：{got}"
    assert got["rows"] == [3, 3], f"行列没恢复：{got}"
    assert got["firstText"] == "序号", f"左上格内容应保留：{got}"


def f14_12_merge_on_a_single_cell_is_a_no_op(page):
    """★ 反向：只选一格时合并不应有副作用（否则用户误点就把表改了）。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const before = T.serialize(t);
        const ok = T.mergeCells(t, [t.querySelector('td')]);
        return { ok, changed: T.serialize(t) !== before };
    }""", _TBL_HTML)
    assert got["ok"] is False and got["changed"] is False, f"单格合并产生了副作用：{got}"
```

（加进 `CASES["f14"]`。）

- [ ] **Step 2: 跑到红** —— `f14_10` 红（`mergeCells is not a function`）。

- [ ] **Step 3: 实现**

`table-editor.js` 追加：

```javascript
    /** 给 `anchor`→`focus` 之间的**矩形区域**加选中态（不改结构）。 */
    function selectRange(tableEl, anchor, focus) {
        tableEl.querySelectorAll('.rvb-cell-sel').forEach((c) => c.classList.remove('rvb-cell-sel'));
        const g = _grid(tableEl);
        if (g.err) return false;
        const rows = [...tableEl.querySelectorAll('tr')];
        const r1 = rows.indexOf(anchor.closest('tr')), r2 = rows.indexOf(focus.closest('tr'));
        const c1 = g.grid[r1].indexOf(anchor), c2 = g.grid[r2].indexOf(focus);
        const [ra, rb] = [Math.min(r1, r2), Math.max(r1, r2)];
        const [ca, cb] = [Math.min(c1, c2), Math.max(c1, c2)];
        for (let r = ra; r <= rb; r++) {
            for (let c = ca; c <= cb; c++) {
                const cell = g.grid[r] && g.grid[r][c];
                if (cell) cell.classList.add('rvb-cell-sel');
            }
        }
        return true;
    }

    /** 合并选中的矩形区域：左上格设 span，**其余格删除**；少于 2 格 ⇒ no-op（返回 false）。 */
    function mergeCells(tableEl, selected) {
        const uniq = [...new Set(selected)];
        if (uniq.length < 2) return false;
        const g = _grid(tableEl);
        if (g.err) return false;
        const rows = [...tableEl.querySelectorAll('tr')];
        const rs = uniq.map((c) => rows.indexOf(c.closest('tr')));
        const cs = uniq.map((c) => g.grid[rows.indexOf(c.closest('tr'))].indexOf(c));
        const r0 = Math.min(...rs), r1 = Math.max(...rs);
        const c0 = Math.min(...cs), c1 = Math.max(...cs);
        const tl = g.grid[r0][c0];
        if (!tl) return false;
        const nrs = r1 - r0 + 1, ncs = c1 - c0 + 1;
        tl.setAttribute('rowspan', String(nrs));
        tl.setAttribute('colspan', String(ncs));
        // 删掉区域内**除左上格**以外的所有格（含跨出选区的合并格 —— 一律整格删除）
        for (let r = r0; r <= r1; r++) {
            for (let c = c0; c <= c1; c++) {
                const cell = g.grid[r] && g.grid[r][c];
                if (cell && cell !== tl) cell.remove();
            }
        }
        // rowspan=1 / colspan=1 没有语义，清掉（否则 HTML 里留噪声属性）
        if (nrs === 1) tl.removeAttribute('rowspan');
        if (ncs === 1) tl.removeAttribute('colspan');
        return true;
    }

    /** 拆分一个合并格：按 span 在**同行/同列**补出空格。span 全为 1 ⇒ no-op。 */
    function splitCell(tableEl, cell) {
        const rs = parseInt(cell.getAttribute('rowspan') || '1', 10);
        const cs = parseInt(cell.getAttribute('colspan') || '1', 10);
        if (rs === 1 && cs === 1) return false;
        const tr = cell.closest('tr');
        const rows = [...tableEl.querySelectorAll('tr')];
        const r0 = rows.indexOf(tr);
        const g = _grid(tableEl);
        const c0 = g.grid[r0].indexOf(cell);
        // 同行右侧补 (cs-1) 个空格
        for (let j = 1; j < cs; j++) tr.insertBefore(document.createElement('td'), cell.nextSibling);
        // 其下 (rs-1) 行在 c0 处各补 cs 个空格
        for (let i = 1; i < rs; i++) {
            const row = rows[r0 + i];
            const ref = g.grid[r0 + i] && g.grid[r0 + i][c0];
            for (let j = 0; j < cs; j++) {
                const td = document.createElement('td');
                if (ref) row.insertBefore(td, ref);
                else row.appendChild(td);
            }
        }
        cell.removeAttribute('rowspan');
        cell.removeAttribute('colspan');
        return true;
    }
```

（注册进 `window.tableEditor`。）

`review-blocks.js` 追加选择与撤销：

```js
        /** 表格编辑态的选择：点 = 单格；Shift+点 = 以已选为锚点扩成矩形区域。
         *  ⚠️ 选中态存在**元素 class**上（`rvb-cell-sel`），不进 Alpine 状态 ——
         *     本仓既定口径：Alpine 状态里只放下标/布尔，DOM 元素会因 Proxy 包壳出问题。 */
        _selectTable(anchorCell) {
            const t = this._tableEl();
            if (!t) return;
            const focus = anchorCell || t.querySelector('.rvb-cell-sel') || t.querySelector('td,th');
            const anchor = this._selAnchor || focus;
            window.tableEditor.selectRange(t, anchor, focus);
            this._selAnchor = focus;
        },

        /** 撤销：弹出最近一次快照整体还原（spec §2.4；**只覆盖结构操作**，不覆盖格内文字）。 */
        tableUndo() {
            const snaps = this._tableSnapshots || [];
            if (snaps.length <= 1) return;
            snaps.pop();
            this._restoreTable(snaps[snaps.length - 1]);
            this.submitError = '';
        },
```

并在 `enterEdit` 的表格分支末尾、以及 `_restoreTable` 里调用 `this._selectTable()`；表格容器上加点击委托：

在 `_tableHost()` 之后新增：

```js
        /** 表格单元格的点击 / Shift+点：由容器**事件委托**处理（格是动态生成的，逐个绑会随重建失效） */
        onTableCellClick(ev) {
            const cell = ev.target.closest('td,th');
            if (!cell) return;
            const t = this._tableEl();
            if (!t || !t.contains(cell)) return;
            if (!ev.shiftKey) this._selAnchor = cell;
            this._selectTable(cell);
        },
```

模板：给 `.rvb-table-edit` 容器加 `@click="onTableCellClick($event)"`；工具条加合并/拆分/撤销三枚。

> ⚠️ **插入锚点要写死**：`<span class="rvb-sep"></span>` 在表格工具条里**有多处**（Task 4 已加过一枚），
> 按它插入会插错位置。**插在 Task 4 那枚「- 列」按钮之后**（即 `<button ... title="删除选中格所在的整列">- 列</button>` 那一行之后）：

```html
                                <span class="rvb-sep"></span>
                                <button type="button" @click="tableOp('merge')" style="margin:0"
                                        title="合并选中的单元格">合并</button>
                                <button type="button" @click="tableOp('split')" style="margin:0"
                                        title="拆分选中的单元格">拆分</button>
                                <button type="button" @click="tableUndo()" style="margin:0"
                                        title="撤销上一次表格结构操作">撤销</button>
```

`tableOp` 的 switch 增加两例（**合并/拆分也要压快照**）：

```js
                case 'merge': ok = window.tableEditor.mergeCells(
                    t, [...t.querySelectorAll('.rvb-cell-sel')]); break;
                case 'split': ok = window.tableEditor.splitCell(t, cell); break;
```

> **本 Task 另需补一条注释**（Task 2 修正轮的裁定）：`validate` 里的**「空洞」判据**在当前输入下**不可达**
> —— Task 2 实现者穷举 2~3 行 × 1~3 列 × span∈{1,2,3} 共 **270672** 张表、**0 例**命中空洞分支
> （机制：每行单元格恒填最左空位 ⇒ 占位形态恒为「0..k 无洞」）。**保留它**是因为**本 Task 的结构操作
> 有可能造出空洞**（`insertCol` 在合并格内部返回 false、`deleteCols` 跨合并格递减 `colspan`、`mergeCells` 删格）。
> 请在判据处补注释写明这一层关系；**若到 Task 6 结束仍构造不出空洞，则按 YAGNI 删除**（届时它连守卫作用都没有）。

`app.css` 加选中态：

```css
/* 表格编辑器：单元格选中态（合并/对齐都作用于选区） */
.rvb-cell-sel { background: #fff3e0; outline: 1px solid #e8710a; }
```

**版本号**：`app.css?v=52` → `?v=53`、`review-blocks.js?v=13` → `?v=14`、`table-editor.js?v=2` → `?v=3`。

- [ ] **Step 4: 跑到绿** —— `== f14: 12/12 passed ==`；`pyright` 0 errors
- [ ] **Step 5: 提交**

```bash
git add static/components/table-editor.js static/components/review-blocks.js app/templates/partials/ocr_review_blocks.html static/app.css app/templates/base.html scripts/probe_spec_ui.py
git commit -m "feat: 表格单元格选择/合并/拆分 + 结构操作撤销"
```

---

## 阶段四：内容级操作与出口判据

### Task 6: 删除格内图片 · 格内对齐 · 格内 B/I/S · 出口判据走查

**Files:**
- Modify: `static/components/table-editor.js`、`static/components/review-blocks.js`、`app/templates/partials/ocr_review_blocks.html`、`app/templates/base.html`
- Test: `scripts/probe_spec_ui.py`（f14 追加）
- Modify: `TODOS.md`

**Interfaces:**
- Produces: `tableEditor.removeImages(cells) -> number`（返回删除张数）、`tableEditor.applyAlign(cells, value)`；`review-blocks.js` 的 `tableAlign(v)`、`tableExec(cmd)`。

- [ ] **Step 1: 追加用例（含**出口判据**）**

```python
def f14_13_remove_images_in_selected_cells(page):
    """★ 删图：只删**选区格内**的 `<img>`，其余格不动，且**文件不删**（spec §19）。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const imgCell = t.querySelector('img').closest('td');
        const n = T.removeImages([imgCell]);
        return { n, imgs: t.querySelectorAll('img').length, valid: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["n"] == 1 and got["imgs"] == 0, f"删图结果不对：{got}"
    assert got["valid"] is True, "删图把结构弄坏了"


def f14_14_align_applies_to_cell_text_only(page):
    """★ 对齐作用于**格内文字**（spec §8.6）；**表格整体恒居中**，不提供按钮。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const cell = t.querySelector('td');
        T.applyAlign([cell], 'right');
        const out = T.serialize(t);
        return { style: cell.style.textAlign, hasTableAlign: /<table[^>]*text-align/.test(out) };
    }""", _TBL_HTML)
    assert got["style"] == "right", f"格内对齐没生效：{got}"
    assert got["hasTableAlign"] is False, "对齐写到 <table> 上了（应为格内文字）"


def f14_15_exit_criterion_image_row_can_be_removed(page):
    """★★ **出口判据（spec §18）**：「行列被当成图片塞进合并格」能被**完整修好**。

    走一遍真实修法：真实产物里「图 形」整列都是 `<img>` ⇒ 选中该列任一格 → 删列
    ⇒ 该列消失、表结构仍合法、且**没有残留的空格**。
    """
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const imgCell = t.querySelector('img').closest('td');      // 「图 形」列
        const ok = T.deleteCols(t, [imgCell]);
        const v = T.validate(t);
        const widths = [...t.querySelectorAll('tr')].map(r => r.children.length);
        return { ok, valid: v.ok, reason: v.reason, widths,
                 imgs: t.querySelectorAll('img').length,
                 text: t.textContent.replace(/\\s+/g, '') };
    }""", _TBL_HTML)
    assert got["ok"] is True and got["valid"] is True, f"删列不合法：{got}"
    assert got["widths"] == [2, 2], f"列没删干净/留了空格：{got}"
    assert got["imgs"] == 0, f"图片列没清掉：{got}"
    assert "序号" in got["text"] and "紧急避难设施" in got["text"], f"误删了内容：{got}"


def f14_16_saving_a_table_edit_does_not_change_the_clause_set(page):
    """★ spec §2.6：**表格编辑不改变切分** —— 保存前后条号集合必须相同。

    切分预览与入库条数的一致性（上游 §18 的 P2 铰链）靠这条延续：表格 HTML 不产生
    新条文号，所以「编辑表格」不该让任何条文凭空出现或消失。

    ⚠️ 直接打 `/preview` 取口径（不读 UI 的预览列表）—— 避开 400ms 防抖带来的时序假红。
    """
    task_id = _rvb_seed(page, "14d", with_table=True)
    assert task_id is not None, "前置不成立"

    def _clause_nos():
        return page.evaluate("""async ([tid]) => {
            const r = await fetch('/import/review/' + tid + '/preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({edits: {}})});
            return (await r.json()).clauses.map((c) => c.clause_no).sort();
        }""", [task_id])

    before = _clause_nos()
    assert before, "前置：预览为空，无法对账"

    blk = page.locator('[data-block-idx]', has_text="探针表格")
    blk.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000)
    page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('[x-data]'));
        const t = d._tableEl();
        window.tableEditor.deleteCols(t, [t.querySelector('img').closest('td')]);
    }""")
    blk.locator("button:has-text('保存')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 0, 5000), \
        "保存后没退出编辑态"

    after = _clause_nos()
    assert after == before, f"表格编辑改变了切分：{before} -> {after}"
```

（加进 `CASES["f14"]`。）

- [ ] **Step 2: 跑到红** —— `f14_13` 红（`removeImages is not a function`）。

- [ ] **Step 3: 实现**

`table-editor.js` 追加：

```javascript
    /** 删除 `cells` 覆盖到的格里的 `<img>`。**不删文件**（spec §19：物理清理在确认导入之后，
     *  编辑期必须可撤回）。返回删除的张数。 */
    function removeImages(cells) {
        let n = 0;
        for (const c of cells) {
            c.querySelectorAll('img').forEach((img) => { img.remove(); n++; });
        }
        return n;
    }

    /** 格内文字对齐（spec §8.6）。`value` 为 `'left' | 'center' | 'right' | null`（null=清）。 */
    function applyAlign(cells, value) {
        for (const c of cells) c.style.textAlign = value || '';
        return true;
    }
```

（注册进 `window.tableEditor`。）

`review-blocks.js` 加两个入口：

```js
        /** 表内对齐（作用于**选中格**；空选区回落到单格） */
        tableAlign(v) {
            const t = this._tableEl();
            if (!t) return;
            const cells = [...t.querySelectorAll('.rvb-cell-sel')];
            window.tableEditor.applyAlign(cells.length ? cells : [t.querySelector('td,th')], v);
            this._tableSnapshots.push(t.outerHTML);
        },

        /** 表内 B/I/S：与行编辑器同一路径（`@mousedown.prevent` 保选区）。
         *  ⚠️ contenteditable 的格子失焦会让选区塌掉 ⇒ 模板上必须写 `@mousedown.prevent`。 */
        tableExec(cmd) {
            const t = this._tableEl();
            if (!t) return;
            const sel = window.getSelection();
            if (!sel || !sel.rangeCount) return;
            document.execCommand(cmd, false, null);
            this._tableSnapshots.push(t.outerHTML);
        },

        /** 删图（作用于选中格；空选区回落到单格） */
        tableRemoveImages() {
            const t = this._tableEl();
            if (!t) return;
            const cells = [...t.querySelectorAll('.rvb-cell-sel')];
            const n = window.tableEditor.removeImages(cells.length ? cells : [t.querySelector('td,th')]);
            if (n) this._tableSnapshots.push(t.outerHTML);
            this.tableError = n ? '' : '选中的格子里没有图片';
        },
```

模板工具条补齐。

> ⚠️ **插入锚点要写死**（同 Task 5 的理由：`<span class="rvb-sep">` 有多处）：**插在 Task 5 那枚
> 「撤销」按钮之后**（即 `<button ... title="撤销上一次表格结构操作">撤销</button>` 那一行之后）：

```html
                                <span class="rvb-sep"></span>
                                <button type="button" title="粗体" @mousedown.prevent @click="tableExec('bold')"
                                        style="margin:0"><strong>B</strong></button>
                                <button type="button" title="斜体" @mousedown.prevent @click="tableExec('italic')"
                                        style="margin:0"><span class="rvb-btn-i">I</span></button>
                                <button type="button" title="删除线" @mousedown.prevent @click="tableExec('strikeThrough')"
                                        style="margin:0"><s>S</s></button>
                                <span class="rvb-sep"></span>
                                <button type="button" @click="tableAlign(null)" style="margin:0" title="格内文字左对齐">左</button>
                                <button type="button" @click="tableAlign('center')" style="margin:0" title="格内文字居中">中</button>
                                <button type="button" @click="tableAlign('right')" style="margin:0" title="格内文字右对齐">右</button>
                                <span class="rvb-sep"></span>
                                <button type="button" @click="tableRemoveImages()" style="margin:0"
                                        title="删除选中格里的图片（不删文件）">删图</button>
```

**版本号**：`review-blocks.js?v=14` → `?v=15`、`table-editor.js?v=3` → `?v=4`。

`TODOS.md` 末尾新增：

```markdown
## T42 — P3 表格编辑器的已知行为与遗留（2026-10-05 登记）

- **已知行为（不是 bug）**：保存会把表格重排为**单行 HTML**（md 里可能是跨行的）。
  实测：跨行版与单行版经应用真实渲染路径（`mdRender.renderHtml`）结果完全相同
  （高/宽/行列数/可见文本皆等，容器 `white-space: normal`）⇒ **任何界面都看不出区别**，
  差异只在存储的 `content` 源码里。详见 spec §2.5。
- **遗留**：
  1. `insertCol` / `deleteCols` 落在**合并格内部**时一律**拒绝**（返回 false，提示"该操作在当前选区不可用"）而不是猜行为；
  2. 表格工具条 13 枚按钮在窄中栏可能折行 —— 用户裁定的出口是**折叠切分栏**，未做响应式分支；
  3. **不覆盖**格内文字的撤销（`tableUndo` 只回滚结构操作）；
  4. 表格内公式（`<span class="katex">`）不参与编辑，属 P4。
- **Blocked by**：无。
```

> **关于「删图不删文件」（spec §19）**：本 Task **不写任何文件删除代码路径** —— 编辑期只操作 DOM，
> 物理清理仍在「确认导入之后」（spec §19 已定）。⇒ 该不变式由**构造**保证，不由探针断言
> （探针的隔离实例里那张合成图 `imgs/t.jpg` 本就不存在，断言「文件还在」没有意义）。
> 评审时请按「diff 里没有任何删除文件的调用」核对这一点。

- [ ] **Step 4: 跑到绿** —— `== f14: 16/16 passed ==`；`pyright` 0 errors
- [ ] **Step 5: 提交**

```bash
git add static/components/table-editor.js static/components/review-blocks.js app/templates/partials/ocr_review_blocks.html app/templates/base.html scripts/probe_spec_ui.py TODOS.md
git commit -m "feat: 表格删格内图片/格内对齐/BIS + 出口判据用例（T42 登记）"
```

---

## 收尾（不属于任何 Task，但必须做）

- [ ] **全量探针**：`f14`、`f13`、`f12`、`f10`、`f11` 各跑一遍（**每轮前重新 `cp` 隔离库**），确认没有跨组回归 —— 尤其 `f13` 组（同一页面、共享 `_rvb_seed`，本次给它加了 opt-in 参数）。
- [ ] **全量回归**：`D:/Python/python.exe -m pytest tests/ -q`（基线 **1461 passed** + 本计划新增 Python 用例）。
- [ ] **真机核对**：在隔离实例上打开一个**有表格**的审查页（`d66c2e0b` 有 24 张表），走一遍：进编辑 → 删掉「图 形」列 → 保存 → 右下切分预览条数不变、块内容正确。
- [ ] 提示用户 **Ctrl+F5**（`app.css` / `review-blocks.js` / `table-editor.js` 三处版本号都已递增）。
- [ ] 按 T41 的教训检查：本计划**没有**给 `layout.json` 新增字段之外的结构变更；`md_html` 已随 Task 1 交付回填步骤（`scripts/backfill_layout.py` 重跑即补）。
