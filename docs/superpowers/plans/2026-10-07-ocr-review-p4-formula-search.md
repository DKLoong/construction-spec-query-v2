# P4 余项：公式块编辑 + 跨块搜索/替换 —— 实施计划

## Context

OCR 审查页的块编辑器已交付 P0–P3 与 P4 图片线（9 个 Task，`7c3d200`…`d998fc3`）。上游设计
spec `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md` 的 §18 把 **P4** 定义为
「图片 / 公式 / 搜索」，其中图片线已交付，**公式与搜索/替换跨块两项至今未开工**（spec §18 末尾
与 `TODOS.md` T44 都显式标注）。

要解决的问题有两个，都是实测出来的：

1. **公式块改不了**。OCR 把公式作为独立块下发（`display_formula`，真实夹具 21 块、全部已锚定），
   但它不在后端可编辑白名单里 ⇒ 前端只显示「该块类型暂不支持编辑」。而 OCR 的公式识别并不
   完美，用户看到错认的 LaTeX 却无处可改。
2. **块页没有搜索**。旧源码审查页（`.md` 上传走的那条）有完整搜索/替换工具条，但**版面块审查页
   一个搜索都没有** —— 块页没有 textarea，旧工具条的「源码偏移」模型在此页不成立。扫描件是
   主路径，恰恰是搜索最需要的地方。

预期结果：块页能就地编辑正文公式（含表格内公式），并能跨块搜索、高亮、跳转、批量替换；
P4 因此可以整体归档。

## 已定决策（用户已裁定，不要重开）

| # | 决策 | 理由（用户选择时给的） |
| --- | --- | --- |
| D1 | **表格内公式 = 工具条加「公式」按钮 → 弹窗**（LaTeX 输入 + 实时预览）→ 插入/替换**选中格**里的公式。一个入口同时覆盖新增与修改 | 一个按钮盖掉「新增」与「改已有的」；用户不必先分辨格内有没有公式 |
| D2 | **搜索放开、替换收紧**：搜索覆盖所有**进 md 的块**，替换只作用于**可编辑块**（文本/表格/公式）；命中不可编辑块时高亮并提示改不了 | 搜索只读无风险；替换必须有写路径，否则改动落不进 md = 静默丢失 |
| D3 | **两项一次交付**（一个计划、两个阶段，各自独立 Task 提交） | spec §18 把两项并列在同一 P4 行、出口判据只有一条 |

## 已实测的事实（本计划的依据，全部有命令输出）

### R1 已解决：公式块的 label

spec §16 把「公式块 `block_label` 名未实测」列为 **R1 未决风险**，并猜 `formula` / `equation`。
**实测答案是 `display_formula`**，旧猜测两个都是错的：

- 真实夹具 `data/_ocr_reference/raw.jsonl`（19 页切片）与 `full.raw.jsonl`（57 页），经
  `extract_layout` 后 label 分布一致：`text` 477 / `number` 57 / `paragraph_title` 47 /
  **`display_formula` 21** / **`formula_number` 21** / `image` 9 / `figure_title` 9 /
  `content` 4 / `vision_footnote` 3 / `table` 1 / `chart` 1。**无 `formula`、无 `equation`**。
- `app/ocr/layout.py:51` 的注释原文即写 **「display_formula → P4」**；`review-blocks.js:436` 已有
  中文映射 `display_formula: '公式'`。
- `formula_number` 在 `DEFAULT_NOT_IN_MARKDOWN`（`layout.py:45`）里 ⇒ **不进 md、恒不可锚定**
  （实测 21 个 `formula_number` 全部 `start_line=None`）⇒ 公式编号本期不可编辑，属预期。
- 陷阱：原始 JSONL 里另有 129 个 `"label": "inline_formula"`，但它在
  `result.layoutParsingResults[].prunedResult.layout_det_res.boxes[].label` —— 那是**原始检测框列表**，
  `extract_layout` 只读 `parsing_res_list[].block_label`，**不读它** ⇒ 不是 block_label，与本期无关。

### 公式块的数据形态（决定后端改动量）

- `display_formula` 的 `content` 与 md 里对应行 **21/21 逐字节相同**，且 **span 恒为单行** `(s, s+1)`。
  形如 `' $$ t_{\mathrm{sf1}}-t_{\mathrm{sf2}}\leq0.05t_{\mathrm{sf,m}} $$ '`（**首尾各一个空格**）。
- ⇒ **不需要**像图片块那样新增 `md_html` 字段（图片块的 `content` 是空串才需要）。后端改动
  只有白名单一行，前端用既有的 `contentOf(b)` 即可。

### KaTeX 校验不会误伤 OCR 公式（决定性证据）

`static/katex/katex.min.js` 版本 **0.18.4**。用真实夹具实测严格模式
（`katex.renderToString(tex, {throwOnError:true, displayMode:true})`）：
**21 个块级公式 + 177 个行内片段，失败 0 例**。⇒ 用严格渲染做「非法 LaTeX 拦截」安全。

- 页面现有入口 `window.mdRender.katexize(root)`（`md-render.js:46-69`）作用在 **DOM 容器**、且
  `throwOnError:false`（非法公式**静默保留原文、不抛也不返回布尔**）⇒ **不能**用它判合法性。
- 全局 `window.katex` 已由 base.html 加载但**全仓零调用点**；校验须自己接 `renderToString`。

### ⚠️ 已确认的两个硬陷阱

1. **`md-inline.js` 的 `domToMd` 不认 `span`**（只认 `b/strong`、`i/em`、`s/strike/del`、`br`，
   `:125-143`）。KaTeX 渲染出的 `<span class="katex">` 若落进行编辑器，其内文本节点会被展平
   拼回 md ⇒ **毁掉公式**。⇒ 公式块**必须走独立分支**（自己的编辑器宿主 + 自己的保存分支），
   **绝不能复用行编辑器**。这是图片块已经确立的模式。
2. **`_rvb_seed` 的「门槛隔离」样本会被本改动打穿**。`scripts/probe_spec_ui.py:1636` 造了一个
   `{"label": "display_formula", "content": "非白名单但有 span", start_line:1, end_line:2}` 的合成块，
   `f13_7`（`:1987-2018`）断言它**没有**「纠正」按钮，用来证明「白名单门槛」真的在起作用。
   **一旦把 `display_formula` 加进 `EDITABLE_TEXT_LABELS`，这个样本就变成白名单内的块 ⇒ `f13_7`
   必然变红**（且是**正确地**红：它的前提变了）。必须在同一 Task 里把该合成块的 label 换成
   一个**仍不在白名单**的 label（用 `chart`：它既不在 `EDITABLE_TEXT_LABELS`、
   也不在 `DEFAULT_NOT_IN_MARKDOWN`，语义仍是「非白名单但有 span」），并同步 f13_7 的断言文案。

### 另外两处已实测的现状（都要在本期处置）

- **`.search-highlight` 在生产 CSS 里零样式**：`static/app.css` 无该规则，只有 demo 页
  `ui-demo-import-ocr.html:87` 有（`background:#f2d8a7`）。块页要用就得新增规则。
- **`protectTildes` 会污染含 `~` 的公式**（既有缺陷，非本期引入）：`md-render.js:29-31` 的
  `LONE_TILDE = /(?<!~)~(?![~])/g` 对**整篇 md** 生效，包括 `$$…$$` 内部；而 `~` 在 LaTeX 里是
  不断行空格。实测 `data/outputs/7c5b6ac9/7c5b6ac9.md` 的第 34 个块级公式
  （`…\mathrm{~}…`）被换成私有区哨兵字符 U+E000 后才交给 KaTeX ⇒ 该公式渲染成未知字符。
  77 个块级公式里命中 **1** 个；19 页夹具命中 0 个。
  **本期处置：登记 TODOS，不在本期修**（改 `protectTildes` 需重新验证整条渲染管线，
  收益 1/77；且本期的公式预览走**原始 LaTeX**，不受此影响）。
- **陈旧文案**：`app/templates/partials/ocr_review_blocks.html:389` 仍写
  「原型阶段：只预览，未落盘、未插入单元格」，与已交付的 T7 行为矛盾（该字符串自 `acc4138`
  起未更新）。

## Global Constraints

来源：项目 `CLAUDE.md`（全局 + 项目）+ 本仓已固化的教训。**每条都对应一次已发生的事故。**

- **TDD**：先写失败用例再写实现。每个 Task 提交前跑**本次变更关联的增量测试**，并跑
  **`pyright`**（**必须是仓库根的 CLI `pyright` 1.1.410**；`D:/Python/python.exe -m pyright`
  会报 `No module named pyright`）**不得新增 error**（基线 **0 error**）。
- **单 Task 单提交**，提交信息 `type: 描述`（`feat/fix/test/docs/refactor/chore`）。
- **永不 `git add .`** —— 逐个文件 `git add <路径>`。
- **静态资源版本号**：改 `static/` 下的 js/css 或模板引用后，**必须递增**对应的 `?v=N`。
  当前值（`base.html`）：`app.css?v=59`、`review-blocks.js?v=31`、`table-editor.js?v=6`、
  `md-inline.js?v=1`、`md-render.js?v=14`、`image-picker.js?v=1`、`image-crop.js?v=1`。
  新文件以 `?v=1` 入列。**加载顺序**：`image-picker.js` / `image-crop.js` 必须在
  `review-blocks.js` **之前**（`base.html:82-85` 注释）⇒ 新增的 `formula-editor.js` /
  `text-search.js` 也挂在 `review-blocks.js` 之前。
- ⚠️ **`app.css` 的版本号有「两处」消费方，必须同步递增**（实测）：
  `app/templates/base.html:9`（`?v=59`）与 `app/templates/login.html:9`（`?v=58`）——
  而且**这两处当前就已经不同步**（59 vs 58）。只改 `base.html` 会让登录页长期吃旧缓存。
  本期凡是动 `app.css` 的 Task，**两处一起改**；顺手把登录页对齐到同一号（不改号只对齐亦可，
  但既然要动就统一）。
- **探针运行**：4 个隔离环境变量（`DATABASE_PATH`/`UPLOAD_DIR`/`OUTPUT_DIR`/`LANCE_DB_PATH`，
  值必须含 `_probe_`）+ 端口 **8123** + **不加 `--reload`**；**四个 export 必须在同级前台**
  （写成 `export … && nohup uvicorn … &` 会让变量只进子 shell 而回落到真实 `data/`，
  脚本内 `_require_isolated_paths()` 会拒绝运行）。每轮跑前重拷副本库，跑完杀 8123 并删四个隔离产物。
- **探针 runner 无逐例 try/except**，一例红即中止该组 ⇒ **不得**把结果写成「其余全绿」。
- **JS 无单测基建**（无 package.json）⇒ JS 行为**一律用探针断言**；**纯函数**用 `page.evaluate`
  在页内单元式断言（本仓既定做法）。
- **滚动容器**：本仓 `window` **永不滚动**，块页真正的滚动容器是 `.rvb-blocks`
  （`app.css:916` `flex:1; overflow:auto`）。跳转到命中块**必须**操作这个容器，
  `scrollIntoView` 不等价于「滚到该块」。
- **性能**：`.rvb-block` 上有 `content-visibility: auto`（`app.css:929`），且**块模板内不得读页面级
  状态**（否则 4000 块 × 每次交互重算，实测进编辑 857ms）。搜索高亮**必须走命令式 DOM**，
  不得把 `searchQuery` / `searchHits` 写进 `x-for` 里的绑定。
- **不得改**：`app/parser/md_parser.py`、`app/parser/text_clean.py`、`md-inline.js` 的既有语义、
  `splice` / `align_clauses` 的行为、旧源码审查页 `ocr_review.html` 的既有搜索行为（f11 守网）。
- 不主动装依赖；不改系统配置。

## File Structure

| 文件 | 职责 | 本计划的改动 |
| --- | --- | --- |
| `app/ocr/layout.py` | 锚定 / 拼接 / 落盘 | `EDITABLE_TEXT_LABELS` 加 `display_formula`（**一行**） |
| `static/components/formula-editor.js` | **新文件**：公式的纯函数层（行解析 / 组行 / 合法性校验 / 格内定位），`window.formulaEditor` | 全部 |
| `static/components/text-search.js` | **新文件**：搜索的共享纯函数 + DOM 标记层（`queryRegex` / `markInto` / `clearMarks`），`window.textSearch` | 全部 |
| `static/components/review-blocks.js` | 审查页 Alpine 组件 | 公式块编辑分支（`enterEdit`/`saveEdit`）；表格公式弹窗；搜索/替换整套状态与动作 |
| `app/templates/partials/ocr_review_blocks.html` | 审查页模板 | 公式块编辑态部件；表格工具条「公式」按钮；公式弹窗；搜索工具条；改陈旧文案（`:389`） |
| `static/app.css` | 样式 | `.search-highlight` 规则；公式编辑器 / 公式弹窗样式 |
| `app/templates/base.html` | 静态资源清单 | 引入两个新 js（挂在 `review-blocks.js` **之前**）；递增改过的 `?v=N` |
| `scripts/probe_spec_ui.py` | 探针套件 | 修 `_rvb_seed` 的隔离样本 label；`f13_7` 文案；新增 **f17**（公式）与 **f18**（搜索）两组 |
| `tests/test_layout_anchor.py` | 纯函数测试 | 白名单含 `display_formula` 的断言 |
| `TODOS.md` | 遗留登记 | 新增 T46（`protectTildes` 污染含 `~` 公式）/ T47（`_queryRegex` 旧页与共享模块的重复，若未一并切换） |
| `docs/superpowers/specs/2026-10-04-…-design.md` | 上游设计 spec | §16 R1 标记解决；§8.5 / §12 补实现决定；§18 P4 行标注交付 |
| `docs/superpowers/plans/2026-10-07-ocr-review-p4-formula-search.md` | **新文件** | 本计划的仓库内正式版（Task 0 Step 1 落盘） |

---

# 阶段一：公式块编辑

### Task 0: 落盘计划 + 顺手修陈旧文案（独立提交）

**Files:** Create `docs/superpowers/plans/2026-10-07-ocr-review-p4-formula-search.md`；
Modify `app/templates/partials/ocr_review_blocks.html`（仅 `:389`）

- [ ] **Step 1**：把本计划（去掉本 Step 之前的口头说明）写入上述 plan 文件，提交
      `docs: P4 余项（公式 + 跨块搜索）实施计划`。
- [ ] **Step 2**：把 `:389` 的 `<p class="rvb-pick-pop-tip">原型阶段：只预览，未落盘、未插入单元格</p>`
      改为与已交付行为一致的文案（右键确认后已**落盘并插入当前编辑的单元格**；
      提示里说清「右键单击确认后插入」，与按钮 `title` 同口径）。
- [ ] **Step 3**：递增 `base.html` 中被引用模板的版本号口径不适用（模板不是静态资源），
      但浏览器需强刷；本步只需 `pyright` 干净。
- [ ] **Step 4**：单独提交 `fix: 取图预览提示去掉「只预览未落盘」的过期文案`。

### Task 1: 后端把 `display_formula` 放进可编辑白名单

**Files:** Modify `app/ocr/layout.py`（`EDITABLE_TEXT_LABELS`，`:53-58`）；
Test `tests/test_layout_anchor.py`（末尾追加）

**Interfaces:**
- Consumes: `EDITABLE_TEXT_LABELS`（前端从 `/layout` 拿，**单一来源**，不在 JS 里另写一份）
- Produces: `"display_formula" in EDITABLE_TEXT_LABELS` 为真；`anchor_blocks` / `splice` **零行为变化**

- [ ] **Step 1: 写失败用例**

```python
# ══ 公式块可编辑（P4；spec R1 实测结论：label = display_formula） ══════════

def test_display_formula_is_editable():
    """★ 公式块必须进可编辑白名单 —— 它是 P4 的出口判据之一。

    为什么不是 spec §7.2 猜的 `formula` / `equation`：真实夹具实测 label 只有
    `display_formula`（21 块）与 `formula_number`（21 块，不进 md）。若照抄旧猜测，
    前端白名单与分支会**全部漏判**、公式块永远显示「暂不支持编辑」。
    """
    from app.ocr.layout import EDITABLE_TEXT_LABELS
    assert "display_formula" in EDITABLE_TEXT_LABELS


def test_formula_number_stays_out_of_editable_and_markdown():
    """★ 公式**编号**不得顺手也放开：它不进 md（`DEFAULT_NOT_IN_MARKDOWN`）⇒ 没有可回写的行。"""
    from app.ocr.layout import DEFAULT_NOT_IN_MARKDOWN, EDITABLE_TEXT_LABELS
    assert "formula_number" in DEFAULT_NOT_IN_MARKDOWN
    assert "formula_number" not in EDITABLE_TEXT_LABELS


def test_formula_block_content_equals_md_line():
    """★ 公式块的 `content` 与 md 那一行**逐字节相同**（实测 21/21）⇒ 前端无需 md_html 字段。

    这条钉住「不必给公式块新增落盘字段」这个决定：一旦哪天 OCR 的 content 变成去样式版，
    前端拿到的就不是可回写的原文了，必须同时补 `md_html`（图片块走的就是那条路）。
    """
    base = "正文一。\n $$ a=b $$ \n正文二。"
    bl = [_blk("正文一。", 0), _blk(" $$ a=b $$ ", 1, label="display_formula"),
          _blk("正文二。", 2)]
    anchored, _ = anchor_blocks(base, bl)
    fb = anchored[1]
    assert fb["start_line"] is not None, "公式块没锚上（前置不成立）"
    assert fb["content"] == " $$ a=b $$ ", "content 不等于 md 那一段原文"
```

- [ ] **Step 2: 跑用例确认失败**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py -q -k "formula"`
Expected: `test_display_formula_is_editable` FAIL；另两条应 PASS（哨兵/事实钉）。

- [ ] **Step 3: 实现**：在 `EDITABLE_TEXT_LABELS` 里加 `"display_formula"`，并把该常量上方的
      注释（`layout.py:51`）从「display_formula → P4」改为「display_formula → P4 已交付」，
      注明 label 名的实测来源。

- [ ] **Step 4: 跑通 + 检查既有测试**

Run: `D:/Python/python.exe -m pytest tests/test_layout_anchor.py -q` → 全绿。
Run: `D:/Python/python.exe -m pytest tests/ -q`（**全量**，因为这是被多方消费的常量：
前端白名单、f12/f13 探针计数、`canEdit` 判据）。
注：`tests/test_review_layout_routes.py:61` 的 `test_layout_endpoint_ships_editable_labels`
是**动态比对**（`r.json()["editable_labels"] == sorted(EDITABLE_TEXT_LABELS)`，`:69`）⇒ 不破，
但它正是「前端不另写一份白名单」的守网，务必确认它绿。

- [ ] **Step 5: 同步修探针夹具（**就在这个 Task 里做，不要留给探针 Task**）**

`scripts/probe_spec_ui.py:1636` 的合成块 label 由 `display_formula` 改为 **`chart`**
（新的「非白名单但有 span」样本），并更新 `:1631-1635` 与 `f13_7`（`:1987-2018`）里
「display_formula」相关的注释与断言文案。**理由**：`f13_7` 靠这个样本证明白名单门槛真的在
起作用；不改 label 它必然（且正确地）变红，而「红」会被误读成回归。
Run（隔离实例下）：`D:/Python/python.exe scripts/probe_spec_ui.py f13` → 24/24。

### Task 2: `formula-editor.js` 纯函数层

**Files:** Create `static/components/formula-editor.js`；Modify `app/templates/base.html`（挂载）

**Interfaces — Produces `window.formulaEditor`：**

| 函数 | 签名 | 语义 |
| --- | --- | --- |
| `parseLine` | `(mdLine) -> {indent, lead, tex, tail, display}` | 拆开一行公式：`lead`/`tail` 是**原样的**首尾空白，`tex` 是 `$$…$$` 或 `$…$` 的内部原文，`display` 标记块级。**不是公式行返回 null** |
| `buildLine` | `(tex, display, srcLine) -> string` | 组回一行：**保留 `srcLine` 的首尾空白**（只替换公式体），无法解析时回落 `` ` $$ tex $$ ` ``。`srcLine` 一律取 `contentOf(b)`（前端手里唯一的源；实测与 md 行 21/21 逐字节相同） |
| `validate` | `(tex, display) -> {ok, error}` | 用 `window.katex.renderToString(tex, {throwOnError:true, displayMode:display})` try/catch；失败返回 `error` 文案（取 KaTeX 的 message 首行） |
| `findInText` | `(text) -> {start, end, tex, display} \| null` | 在**任意文本**（表格格内文字）里定位第一处 `$$…$$` / `$…$`，供弹窗预填与替换 |
| `renderPreview` | `(host, tex, display) -> {ok, error}` | 把校验结果渲染进预览宿主；非法时宿主显示错误而不是抛 |

- [ ] **Step 1: 写用例（探针内单元式，`page.evaluate` 直接调纯函数）**

  本仓 JS 无单测基建，纯函数的**用例写在 f17 组里**（Task 6），本 Task 先用一个**一次性**
  的 `page.evaluate` 冒烟（跑完即弃，不入库），确认四个函数存在且基本行为正确。

- [ ] **Step 2: 实现**（照抄 `table-editor.js` 的模块形态：IIFE + `window.formulaEditor` 导出 +
      文件头注释写清「为什么单独成文件：探针是唯一的 JS 测试宿主」）。

  实现要点：
  - `parseLine` 用 `/^([\s\S]*?)(\$\$[\s\S]*?\$\$|\$[\s\S]*?\$)([\s\S]*)$/` **锚定整行**，
    且要求定界符外只有空白才认（`lead.trim()==='' && tail.trim()===''`）——
    否则「正文里含 `$…$` 的行」会被误当成公式行。
  - `validate` 必须用**原始 LaTeX**：**不得**先过 `md-render.js` 的 `fixOrphanSup` /
    `protectTildes`（实测 `protectTildes` 会把 `\mathrm{~}` 的 `~` 换成 U+E000，
    把合法公式验成非法）。
  - 所有函数不得触碰 DOM（`renderPreview` 除外），保证能被 `page.evaluate` 直接断言。

- [ ] **Step 3**: `base.html` 在 `review-blocks.js` **之前**挂
      `<script src="/static/components/formula-editor.js?v=1"></script>`，并递增
      `review-blocks.js?v=31 → v=32`（同 Task 会改它，见 Task 3）。

### Task 3: 公式块编辑态（就地展开）

**Files:** Modify `static/components/review-blocks.js`（`enterEdit` 分派、`saveEdit` 分支、
新增 `_formula*` 方法）、`app/templates/partials/ocr_review_blocks.html`（公式编辑态部件）、
`static/app.css`

**Interfaces:**
- Consumes: `window.formulaEditor.parseLine/buildLine/validate/renderPreview`；`contentOf(b)`；
  `mdInline.wrap(inner, align)` / `mdInline.parseLine(line)`（对齐包裹，**同行、不新增行**）
- Produces: 公式块进入编辑态后，块内出现「LaTeX 编辑框 + 实时预览 + 校验错误行」；
  `saveEdit` 把结果写进 `edits[b.__i]`

**交互（照抄图片块的既定范式，与文本/表格一致）：** 先点「纠正」→ 出工具条与编辑器 → 「取消/保存」。

- [ ] **Step 1: 写失败用例**：见 f17（Task 6）。先写 f17 的前 3 条并在本 Task 内跑红。

- [ ] **Step 2: 模板**：在 `ocr_review_blocks.html` 的图片块部件之后，加
      `<template x-if="b.label === 'display_formula'">`（**用 `x-if` 不用 `x-show`**：公式块只占少数，
      `x-show` 会白带节点 —— 与表格/图片同理）包一层 `.rvb-fml-wrap`，内含：

  ```html
  <div class="rvb-tools">
      <button type="button" @click="formulaCancelAlign()" ...>左</button>
      <button type="button" @click="formulaAlign('center')" ...>中</button>
      <button type="button" @click="formulaAlign('right')" ...>右</button>
  </div>
  <textarea class="rvb-fml-src" x-ref="…" @input="formulaPreview()"></textarea>
  <p class="rvb-fml-err" x-show="formulaError" x-text="formulaError" style="color:#c00"></p>
  <div class="rvb-fml-preview"></div>
  ```

  ⚠️ **不要**给编辑框挂 `mdInline` 的行结构（`.rvb-lines`）—— 那会把它送进行编辑器，
  命中上面记的 `domToMd` 不认 `span` 的陷阱。

- [ ] **Step 3: JS**
  - `enterEdit` 在 `isImg` 之前/之后加一条 `b.label === 'display_formula'` 分支，
    走 `_buildFormulaEditor(b)`：用 `formulaEditor.parseLine(contentOf(b))` 取 `tex` 预填 textarea，
    记录 `_fmlBaseline = tex` 与原始行的 `lead/tail`，渲染一次预览。
  - `formulaPreview()`：`validate` → 写 `formulaError`（非法时）并 `renderPreview`。
  - `formulaAlign(v)`：设置 `_fmlAlign`（对齐**不立即写行**，与文本行一致，保存时统一落）。
  - `saveEdit()` 加公式分支：读 textarea 的 `tex` → **若 `tex === _fmlBaseline` 且未点过对齐，
    则一律不写 `edits`**（未改动保存不写，与图片块 `f16_3` 同口径 —— 这样就没有任何
    往返风险）；否则 `validate` 失败 ⇒ **保持编辑态、只在块内报错、不写 edits**（沿用表格编辑态
    「校验失败保留编辑态、块内报错」的既有形状）；合法 ⇒
    `edits[__i] = mdInline.wrap(formulaEditor.buildLine(tex, display, srcLine), align)`。

- [ ] **Step 4: CSS**：`.rvb-fml-src` 用等宽字体、`width:100%`；`.rvb-fml-preview` 给浅底色与最小高度；
  `.rvb-fml-err:empty { display:none }`（照抄 `.rvb-table-err:empty` 的既有写法）。

- [ ] **Step 5: 递增版本号**（`review-blocks.js`、`app.css`），跑 f17 前 3 条转绿。

### Task 4: 公式对齐（左 / 中 / 右）

**Files:** Modify `review-blocks.js`（`formulaAlign` / `saveEdit` 公式分支）、模板（按钮已加）

**Interfaces:** Consumes `mdInline.wrap` / `mdInline.parseLine`（**同行 span，不新增行**）

- [ ] **Step 1**：用例见 f17（对齐后 `$$` 仍被 KaTeX 渲染、条文数与条号序列不变、未编辑块不受影响）。
- [ ] **Step 2**：实现：保存时若 `_fmlAlign` 有值 → `mdInline.wrap(line, align)`；选「左」传 `null`
      （回到无包裹，与文本行的语义一致）。
- [ ] **Step 3**：⚠️ **必须实测的一点**：`<span style="display:block;text-align:center">` 包裹后，
      KaTeX 的 `renderMathInElement` **仍要能找到 `$$…$$`**（它走文本节点扫描）。
      `md-inline.js:23` 的注释与此读法一致，但**这条必须由 f17 断言它真的渲染出 `.katex`**，
      不能只断言「存进去的字符串对」。

### Task 5: 表格内公式弹窗（D1 = A）

**Files:** Modify `ocr_review_blocks.html`（表格工具条加「公式」按钮 + 弹窗）、
`review-blocks.js`（开/关弹窗、落点判定、写入选中格）、`app.css`

**Interfaces:**
- Consumes: `tableEditor`（DOM 即模型）、选中格 `td.rvb-cell-sel`、`formulaEditor.findInText/validate`
- Produces: 弹窗确定后，选中格内：**已有公式 ⇒ 原地替换那一段**；**无公式 ⇒ 在光标处插入 `$…$`**

- [ ] **Step 1: 用例**：见 f17（新增/替换两条 + 非法被拦）。先跑红。
- [ ] **Step 2**：工具条在「删图」之后加 `公式` 按钮（**注意插入锚点写死在具体按钮之后**，
      不要按 `.rvb-sep` 定位 —— 该工具条里有多枚分隔符，按类名定位会插错位置，模板 `:206-207`
      已记过这个坑）。
- [ ] **Step 3**：弹窗**挂 `.rvb-root` 之下**（`position:fixed`）。⚠️ **不要**放进 `.rvb-img-stage`
      或任何 `overflow:auto` 的容器里 —— 会被裁掉（模板 `:372-374` 已记过这个坑）。
      用 `x-if` 控存在性而非只 `x-show`（避免空 `src`/空内容引发多余请求，模板 `:380-384` 的教训）。
- [ ] **Step 4**：落点：`_fmlCell()` 取当前选中格；确定时用 `formulaEditor.findInText(cellText)`：
      命中 ⇒ 用新公式替换 `[start,end)`；未命中 ⇒ 用 `document.execCommand('insertText')` 在
      光标处插入（与行编辑器 `exec` 同一条既有通道）。写入后**不立即序列化** ——
      表格式保存仍走 `saveEdit` 的表格分支（用户点「保存」才落 `edits`）。
- [ ] **Step 5**：行内 / 块级：`findInText` 命中的保留**原定界符**；新增默认**行内** `$…$`，
      弹窗内给「行内 / 块级」二选一（spec §8.5 的「按原公式是行内还是块级分别输出」）。

### Task 6: 探针组 **f17**（公式）

**Files:** Modify `scripts/probe_spec_ui.py`（新增 banner + 用例 + `CASES["f17"]`，位置紧跟 f16 之后）

**夹具（⚠️ 必须新造，实测已确认）**：`_rvb_seed` **不提供任何公式块**。原因是探针的页切片
`_RVB_PAGES = 10`（`:1504`），而真实夹具里 21 个 `display_formula` **全在第 21–54 页**
（分布 `{21:1, 29:1, 30:1, 32:2, 33:1, …, 54:1}`）⇒ `page <= 10` 过滤后**留存 0 个**
（切片共留 76 块，与 `f13` 注释里的「76 块」一致）。

⇒ **照 `with_image` 的既有手法加一个 `with_formula` opt-in**：**不新增块**（保持锚定配对关系
不变），只把前 2–3 个 `display_formula` 块的 `page` 挪进切片内（并给 `bbox`，让左栏也画框）；
**默认关闭** ⇒ 不影响 f12–f16 的既有夹具计数。f16 已用同一手法处理 image 块
（`:1606-1617`，注释里记了「第一版补了 9 个块 ⇒ 18 vs 9 ⇒ 整趟不配」的教训），照抄即可。

用例内**不要靠渲染文本找块**，一律按 `content` 里的 `$$` 或 label 取 `__i`（确定性）。

用例清单（每条一句话 + 断言要点）：

| # | 用例 | 断言要点 |
| --- | --- | --- |
| f17_1 | 公式块有「纠正」入口 | `canEdit` 白名单生效（**本轮修复的主症状**：修前显示「暂不支持编辑」） |
| f17_2 | 进编辑态：编辑框预填**公式本体**（不含 `$$`） | 与 `contentOf` 解析出的 `tex` 一致；行编辑器**没有**被启用（`.rvb-lines` 为空） |
| f17_3 | 未改动直接保存：**不写 edits** | `Object.prototype.hasOwnProperty.call(edits, i) === false`（判别性：只有「未改动不写」这条路才让它绿） |
| f17_4 | 改公式体并保存：edits 里是新公式，且**首尾空白与原行一致** | 直接读 `edits[i]` 字符串比对（**保留 lead/tail** 这条一旦写成规范化 ` $$ x $$ ` 就红） |
| f17_5 | **非法 LaTeX 被拦**：保存后仍在编辑态、块内报错、`edits` 未写 | 三条一起断言（只断言「有报错」会被「保存失败但已退出编辑态」蒙混） |
| f17_6 | 合法但含 KaTeX 不认的字符时的边界（`\notacommand`）| 同上路径，证明不是「只有空串才拦」 |
| f17_7 | 对齐「中」：存进去的是**单行**同行 span，且**渲染出 `.katex`** | 两条一起断言：行数不变 **且** `.rvb-block-bd .katex` 存在（只断言字符串形状会漏掉「包裹后 KaTeX 不再渲染」） |
| f17_8 | 对齐前后：`/preview` 的**条文数与条号序列不变** | 复用 `_rvb_preview`（`:4055`）；这是 spec §8.6/§15.1 的强制判据 |
| f17_9 | 未编辑的公式块：**保存后 md 逐字节不变** | 走真实 UI 流程（**不要**只调纯函数 —— 本仓已记过「纯函数往返不经过组件层绝对化 ⇒ 等于没网」的教训） |
| f17_10 | 表格：工具条有「公式」按钮；无选中格时**拦住并给下一步** | 与取图无编辑目标同口径（用户裁定过：拦住、不静默） |
| f17_11 | 表格：选中空格 → 弹窗输入 → 格内出现 `$…$`（**行内默认**） | 读格内文本；再断言重渲染后 `.katex` 出现 |
| f17_12 | 表格：格里已有公式 → 弹窗预填该公式 → 替换后**只有一个**公式 | 断言格内 `$` 的个数为 2（一对定界符），防「替换变追加」 |
| f17_13 | 表格：公式弹窗对**非法**输入同样被拦 | 与 f17_5 同口径 |
| f17_14 | 弹窗不被容器裁剪 | 断言弹窗的 `getBoundingClientRect()` 在视口内（浮层挂错祖先的判别性用例） |
| f17_15 | 变异证伪：把 `_fmlBaseline` 比较去掉（改成「总是写 edits」）⇒ f17_3 必须变红 | 手工变异一次并记录（本仓要求关键路径有网） |

### Task 7: 阶段一收尾

- [ ] 跑 `f12`→`f13`→`f14`→`f15`→`f16`→`f17` 全绿；`pyright` 0 error；`pytest tests/ -q` 全绿。
- [ ] 提交；在 `TODOS.md` 登记 T46（`protectTildes` 污染含 `~` 的公式，实测 1/77）。

---

# 阶段二：跨块搜索 / 替换

### Task 8: `text-search.js` 共享纯函数 + DOM 标记层

**Files:** Create `static/components/text-search.js`；Modify `base.html`（挂载，在 `review-blocks.js` 前）

**Interfaces — Produces `window.textSearch`：**

| 函数 | 签名 | 语义 |
| --- | --- | --- |
| `queryRegex` | `(q, flags) -> RegExp \| null` | 搜索词 → 正则的**单一来源**（转义元字符）；空串返回 `null` |
| `countIn` | `(text, q) -> number` | 一段文本里的命中数 |
| `markInto` | `(root, q) -> number` | 在 `root` 的**文本节点**里插 `<mark class="search-highlight">`，返回命中数。⚠️ **必须用正则 `exec` 逐段切分，不得用 `split`**（`split` 只在带捕获组时才返回命中片段，会随正则写法**静默错位** —— 本仓踩过） |
| `clearMarks` | `(root) -> void` | 把 `<mark class="search-highlight">` 换回文本节点并 `normalize()` 合并相邻文本节点（**不合并会攒碎片节点**，旧页注释已记） |
| `markAt` | `(root, q, nth) -> Element \| null` | 返回第 `nth` 个 `<mark>`，供「当前项」样式与滚动定位 |

- [ ] **Step 1**：用例在 f18 里（Task 11）。本 Task 先一次性冒烟。
- [ ] **Step 2**：实现（形态照抄 `md-inline.js`/`table-editor.js`：IIFE + `window.textSearch`）。
      纯函数部分（`queryRegex`/`countIn`）与 DOM 部分分开，便于 `page.evaluate` 单测。
- [ ] **Step 3**：`app.css` 新增 `.search-highlight`（现状**生产零样式**；取 demo 页的
      `background:#f2d8a7`，并补当前项样式 `.search-highlight--on`）。

### Task 9: 块页搜索工具条 + 跨块命中与高亮（D2 = A）

**Files:** Modify `ocr_review_blocks.html`（右栏顶部工具条）、`review-blocks.js`、`app.css`

**Interfaces:**
- Consumes: `contentOf(b)`（命中判定的唯一口径）、`textSearch.*`、`.rvb-block-bd`（渲染后宿主）
- Produces: `searchQuery` / `searchHits`（`[{bi, blockIdx}]`）/ `searchIdx` / `matchInfo`；
  命中块计数与**可替换数**分别显示（D2：两者口径不同，必须分别说清）

- [ ] **Step 1: 用例**：f18 前 5 条，先跑红。
- [ ] **Step 2**：工具条放**右栏顶部**（`.rvb-right` 内、`.rvb-blocks` 之前），复用既有
      `.review-toolbar` 类（`app.css:73-92`，已是「控件同行等高基线对齐」的既定样式）。
      搜索框用 `type="search"`，计数用 `<small>`（与旧页同构，便于用户迁移直觉）。
- [ ] **Step 3**：命中范围（D2）：遍历 `blocks`，对 `contentOf(b)` 计数。
      **排除**「不进 md 的块」（`label` ∈ 后端 `not_in_markdown` 名单，`/layout` 已下发 ——
      确认下发的字段名，若未下发则在 `/layout` 响应里补上，保持「单一来源」）。
      例：页码块（`number`）不进计数，避免报出永远改不动的匹配。
- [ ] **Step 4**：高亮**命令式**：`findMatches()` 里先 `clearMarks` 于所有已渲染块，
      再对命中块 `markInto(blockBd)`。⚠️ **不得**把 `searchQuery`/`searchHits` 写进 `x-for`
      里的绑定（4000 块 × 每次输入重算，本仓记过该事故）。
      ⚠️ 块的重渲染（`renderBlock` 走 `x-html`）会**销毁** `<mark>` ⇒ 任何会触发重渲染的动作
      （保存编辑、替换）之后**必须重新调用**高亮。
- [ ] **Step 5**：计数文案：`N 个匹配 · M 个可替换`；`M < N` 时给 `title` 说明
      「其中 N−M 处所在块不可编辑」（D2 的诚实口径）。

### Task 10: 上一个/下一个跳转 + 替换（当前 / 全部）

**Files:** Modify `review-blocks.js`、模板（按钮）、`app.css`（当前项样式）

- [ ] **Step 1: 用例**：f18 中后段，先跑红。
- [ ] **Step 2**：跳转：`nextMatch`/`prevMatch` 更新 `searchIdx`，给第 n 个 `<mark>` 加
      `.search-highlight--on`，并把**`.rvb-blocks` 滚动**到该块（⚠️ **本仓 `window` 永不滚动**，
      真正的滚动容器是 `.rvb-blocks`；`scrollIntoView` 不等价于「滚到该块」）。
      滚动用块元素相对容器的偏移计算（照抄既有 `_scrollRightTo` 的做法）。
- [ ] **Step 3**：替换**当前**：只对**可编辑块**生效；命中在不可编辑块时按钮禁用并说明原因。
      替换写 `edits[b.__i]`，然后重渲染该块并重跑高亮。
- [ ] **Step 4**：替换**全部**：
  - **文本类块**（`label` ∈ `EDITABLE_TEXT_LABELS` 且非 table）：对 `contentOf(b)` 直接替换。
  - **表格块**：⚠️ 不可对 HTML 字符串直接替换（会命中标签与属性）。做法：`tableEditor.parse(src)`
    → 在**文本节点**上替换 → `serialize` 回写成新的 `edits[b.__i]`。文本节点级替换天然不进属性。
  - **公式块**：其 `content` 就是 `$$…$$` 行，按下面 Step 5 的规则处理。
  - ⚠️ **替换值必须用函数式**（`(m) => this.replaceText`）—— 字符串替换值里的 `$&` / `$1`
    会被 JS 当替换模式展开（用户输入 `$&X` 会插进意料之外的内容）。这是旧页已修的缺陷，**必须保留**。
- [ ] **Step 5**：⚠️ **公式块的替换边界**：在 `$$…$$` 内部做纯文本替换可能破坏 LaTeX。
      处置：公式块参与替换**但替换后逐块调用 `formulaEditor.validate`**，非法则该块的替换**跳过**
      并在计数里扣除、提示「K 处因会破坏公式被跳过」。**不得**静默产生非法公式。
- [ ] **Step 6**：替换后刷新切分预览（`schedulePreview()`），并在预览里核对条文数变化。

### Task 11: 探针组 **f18**（跨块搜索/替换）

用例清单：

| # | 用例 | 断言要点 |
| --- | --- | --- |
| f18_1 | 块页有搜索工具条，输入关键词出计数 | 计数与**独立算出**的期望值一致（期望值在探针里用 `page.evaluate` 遍历 `contentOf` 现算，**不写死数字**） |
| f18_2 | 命中处出现高亮，且**表格仍是表格** | `.search-highlight` 数 > 0 **且** `.rvb-block-bd table` 仍存在（旧页曾因整篇转义打塌表格；这里是新页，同样是判别性用例） |
| f18_3 | 清空搜索框：高亮撤掉、块内容不受损 | `<mark>` 归零且块文本与搜索前逐字相同 |
| f18_4 | 重复搜索不叠加 `<mark>` | 连搜两次，`<mark>` 数不变（防「`<mark>` 再包一层」，旧页注释记过该隐患） |
| f18_5 | 不可进 md 的块**不计数**（D2） | 用页码块的内容当关键词搜 ⇒ 计数不含它 |
| f18_6 | 「N 个匹配 · M 个可替换」两数不同且都正确 | 构造一个含不可编辑块的夹具，断言 `N > M` 且文案含两个数 |
| f18_7 | 下一个/上一个：计数更新，且**滚动容器是 `.rvb-blocks`** | 断言 `.rvb-blocks.scrollTop` 变化 **且** `window.scrollY` 为 0（判别性：滚错容器的实现在这条上红） |
| f18_8 | 替换当前：只改可编辑块，`edits` 里出现新值 | 读 `edits[b.__i]` 字符串比对 |
| f18_9 | **替换值字面生效**：替换值填 `$&X` | 读 `edits` 断言含字面 `$&X`（**这是旧页 f11_3 的同款网，必须在新页也有一条**） |
| f18_10 | 全部替换跨块生效 | 至少两个块被改；`/preview` 条数与预期一致 |
| f18_11 | 表格块替换**不碰 HTML 标签** | 关键词故意等于某个属性名/标签名的一部分（如 `td`），断言替换后表格仍能被 `tableEditor.parse` 解析、且标签未变 |
| f18_12 | 公式块替换若会破坏 LaTeX 则**跳过并提示** | 断言该块 `edits` 未变 **且** 界面上有跳过计数 |
| f18_13 | 替换后高亮自动重建 | 替换完 `<mark>` 数不为 0（防「重渲染销毁 mark 后不重建」） |
| f18_14 | 变异证伪：把替换值的函数式写法改成字符串写法 ⇒ f18_9 必红 | 手工变异一次并记录 |
| f18_15 | 变异证伪：把跳转的滚动容器从 `.rvb-blocks` 改成 `window` ⇒ f18_7 必红 | 手工变异一次并记录 |

### Task 12: 文档收尾

- [ ] 上游 spec `docs/superpowers/specs/2026-10-04-ocr-review-block-editor-design.md`：
  - §16 **R1 标记为已解决**，写上实测结论（`display_formula`，附夹具计数）。
  - §7.2 的 label 猜测表（`:269` `| formula | 公式 |`）改为实测名。
  - §8.5 补：内嵌公式入口（表格工具条「公式」按钮 + 弹窗）、校验用 `window.katex.renderToString`
    严格模式（并记录「真实夹具 21+177 全过」这条实测）、公式块**不走行编辑器**的理由。
  - §12 补：块页**原本零搜索**，本次为**新建**；搜索范围与替换范围的口径（D2）。
  - §18 的 P4 行标注交付。
- [ ] `TODOS.md`：T44 追加交付说明；新增 T46（`protectTildes` 污染含 `~` 的公式）；
      若 Task 8 未把旧页切到共享模块，则新增 T47（`_queryRegex` 两处重复）。
- [ ] `pyright` 0 error；提交 `docs: P4 公式 + 跨块搜索交付归档`。

---

## 端到端验证

### 1. 隔离实例（沿用既定配方）

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_spec.db
export DATABASE_PATH="$PWD/data/_probe_spec.db"
export UPLOAD_DIR="$PWD/data/_probe_uploads"
export OUTPUT_DIR="$PWD/data/_probe_outputs"
export LANCE_DB_PATH="$PWD/data/_probe_lance"
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123 &
```

⚠️ 四个 `export` **必须在前台逐条执行**（见 Global Constraints）；**不加 `--reload`**。

### 2. 探针（顺序执行，逐组确认自报条数）

```bash
D:/Python/python.exe scripts/probe_spec_ui.py f17   # 公式，期望 15/15
D:/Python/python.exe scripts/probe_spec_ui.py f18   # 搜索/替换，期望 15/15
# 回归（本轮改动面涉及共享常量与块页模板，这几组必须重跑）
D:/Python/python.exe scripts/probe_spec_ui.py f12   # 只读对照
D:/Python/python.exe scripts/probe_spec_ui.py f13   # 块编辑能力（含被改的夹具样本）
D:/Python/python.exe scripts/probe_spec_ui.py f14   # 表格编辑器
D:/Python/python.exe scripts/probe_spec_ui.py f15   # 拖框取图
D:/Python/python.exe scripts/probe_spec_ui.py f16   # 图片线
D:/Python/python.exe scripts/probe_spec_ui.py f11   # 旧页搜索（不得被破坏）
```

### 3. 后端

```bash
D:/Python/python.exe -m pytest tests/ -q
pyright          # 仓库根执行，期望 0 error
```

### 4. 真机验收（用户，浏览器）

强刷 `Ctrl+F5`（`review-blocks.js` / `formula-editor.js` / `text-search.js` / `app.css` 版本号已递增）。
验收动作：
1. 打开一个**扫描件**待审查任务（非 `.md`），找一个公式块 → 点「纠正」→ 改一个字符 → 看到预览跟着变 → 保存。
2. 故意输一个非法 LaTeX（如 `\frac{1`）→ 应被拦住并有可读提示。
3. 点「中」→ 公式居中且**仍然是一行**（切分预览里条文数不变）。
4. 进表格编辑态 → 选中一个含公式的格 → 点「公式」→ 弹窗预填该公式 → 改 → 确定 → 格内只有一个公式。
5. 搜索一个常见词 → 看到计数与高亮 → 点「下一个」→ 列表滚动到该块 → 「全部替换」。
6. 确认「N 个匹配 · M 个可替换」两个数都对得上。

### 5. 收尾

杀 8123 进程；删 `data/_probe_spec.db`、`data/_probe_uploads`、`data/_probe_outputs`、
`data/_probe_lance`（memory 约束：任务完毕清理非必需临时产物）。
⚠️ 判据是「没有 uvicorn/spawn_main 的**活**进程」，`netstat` 的幽灵行不算（项目 CLAUDE.md §三 已记）。

## spec 逐条落点核对（防漏项）

**§8.5 公式块** —— 六句逐句：

| spec §8.5 原句 | 落点 |
| --- | --- |
| 正文公式**就地展开**：LaTeX 标题 + 输入框 + 「公式预览」+ 取消/保存 | Task 3（编辑器部件；「取消/保存」复用模板 `:173-174` 那排**对所有 label 可见**的按钮） |
| 表格内公式**弹窗**（格太小）同款内容 | Task 5 |
| 预览用 **KaTeX**（现成） | Task 2 `renderPreview` → `window.katex.renderToString`；Task 3 / Task 5 调用 |
| **保存前校验**：渲染失败即非法 ⇒ 阻止保存并提示 | Task 2 `validate`（`throwOnError:true`）；Task 3 / Task 5 各挡一次；f17_5 / f17_6 / f17_13 |
| **定界符**：按行内 / 块级分别 `$…$` / `$$…$$`，**不引入 `\(…\)`** | Task 2 `parseLine` / `buildLine` 的 `display` 标记；Task 5 的「行内 / 块级」二选一 |
| **对齐**：控制**公式的容器**（display 公式由 KaTeX 天然居中） | Task 4（同行 span 包裹，不新增行） |

**§12 搜索/替换改造** —— 四个能力 + 两条既有教训：

| 项 | 落点 |
| --- | --- |
| 匹配计数：跨所有块的 `block_content` | Task 9（口径 = `contentOf(b)`，排除不进 md 的块） |
| 高亮：**DOM 层高亮**（走文本节点，不重渲染） | Task 8 `markInto` / `clearMarks`；Task 9 对命中块施加 |
| 上一个/下一个：**滚动到命中块**并高亮 | Task 10（滚动容器 = `.rvb-blocks`，**不是 `window`**） |
| 全部替换：跨块 + **保持函数式替换值**（防 `$&` 展开） | Task 10 Step 4 |
| 既有教训①：高亮用 **`exec` 逐段切分**，不用 `split`（无捕获组会静默错位） | Task 8 `markInto` 实现即照此，含零宽防死循环 |
| 既有教训②：替换值用 `() => this.replaceText` | Task 10 Step 4；f18_9 钉住 |

**§15 测试策略**：公式与搜索各一组探针（Task 6 / Task 11）；关键路径用**变异测试**证明有网
（f17_15、f18_14、f18_15）。

## 会被打破 / 需要复核的既有测试与探针

| 受影响项 | 会不会破 | 处置 |
| --- | --- | --- |
| **`f13_7`**（`probe_spec_ui.py:1987-2018`） | **必红**（正确地红）：它依赖 `_rvb_seed` 那个 `display_formula` 合成块「非白名单」 | **Task 1 Step 5** 把该合成块 label 换成 `chart`（仍非白名单、非豁免），并同步 `:1631-1635` 注释与 f13_7 文案；跑 f13 确认 24/24 |
| `tests/test_review_layout_routes.py::test_layout_endpoint_ships_editable_labels` | 不破（动态比对常量） | Task 1 Step 4 确认它绿 |
| `f12_3`（块类型标签）/ f13 的「锁定块」计数断言 | 不破：新增可编辑的 `display_formula` 会多出「纠正」按钮，但断言口径是「locked > 0」「fix < 总数」 | 跑 f12 / f13 复核 |
| `f11`（旧源码审查页搜索，4 条） | 不破（**不动 `ocr_review.html`**）；但新增 `.search-highlight` 样式不得改变其 `<mark>` **数量** | Task 8 后跑 f11 确认 4/4 |
| `f14` / `f16`（表格 / 图片） | 不破：新增的公式工具条、弹窗、搜索栏都门控在 `x-if` / `x-show` 内，不影响既有选择器 | Task 5 / Task 9 后各跑一次 |
| `tests/test_ocr_normalize.py` / `test_review_compose.py` | 不破（未改 `normalize_md` / `splice`） | 全量 `pytest` 确认 |

## 风险与未知

| # | 项 | 处置 |
| --- | --- | --- |
| 1 | **对齐包裹后 KaTeX 是否仍渲染 `$$`** | `md-inline.js:23` 注释支持「仍被解析」，但**必须由 f17_7 实测断言 `.katex` 存在**，不能只断言字符串形状 |
| 2 | 公式块**编辑态**里 KaTeX 预览与**保存后**的渲染是否一致 | 预览走 `katex.renderToString`（局部、严格），保存后走 `marked → DOMPurify → katexize`（宽松）。两者对同一 LaTeX 的宽容度不同 ⇒ 可能出现「预览过、保存后显示异常」。f17 需覆盖一条「预览成功且保存后 `.katex` 存在」 |
| 3 | 表格格里公式的**原始文本**在编辑态是否真的还是 `$…$` 字面 | 表格编辑态由 `tableEditor.parse(md_html)` 构建（源是 md 原文，非渲染后 DOM）⇒ 应为字面 `$…$`。**实施第一步先用真实产物（47 张表、单表最多 40 个 `$`）确认**，若发现编辑态里已是 `.katex` span，则弹窗的「预填」需改读 KaTeX 的 MathML `annotation`（`encoding="application/x-tex"`） |
| 4 | `display_formula` 进白名单后，**f12/f13 里可能写死的可编辑块计数**会变 | Task 1 Step 4 跑全量探针核对；有计数断言就改成现算 |
| 5 | 4000 块规模下的搜索性能 | 计数是 O(块数×文本长度) 的纯字符串扫描，每次输入即算 ⇒ **必须防抖**（照抄 `schedulePreview` 的防抖做法），且高亮只对命中块做 |
| 6 | `protectTildes` 污染含 `~` 的公式（1/77） | 本期**不修**，登记 TODOS T46（改它需重新验证整条渲染管线） |
| 7 | 旧页（`.md` 源码审查）与新模块的 `queryRegex` 重复 | 理想是旧页也切到 `text-search.js`（f11 四条例程即守网）。属**可选收尾**，不做则登记 T47 |
| 8 | **用户上传的 `.md`** 里块的 `content` 与 md 行可能首尾空白不同（锚定按 `_norm` 去行尾空白匹配，`layout.py:242`） | 与文本块同源、同待遇：**未编辑 ⇒ `splice` 原行取回，逐字节不变**（有保证）；编辑过则以 `content` 的形态写回（差异只在首尾空白，不影响切分）。本期不改口径，仅在 T46 记为已知边界 |
| 9 | `app.css` 版本号两处（`base.html:9` / `login.html:9`）本来就不一致 | 已写进 Global Constraints：动 `app.css` 的 Task 两处同步递增 |

## 落盘清单（提交粒度）

| 提交 | 内容 |
| --- | --- |
| 1 | `docs: P4 余项（公式 + 跨块搜索）实施计划` |
| 2 | `fix: 取图预览提示去掉「只预览未落盘」的过期文案` |
| 3 | `feat(backend): display_formula 进可编辑白名单 + 探针夹具换 label` |
| 4 | `feat: formula-editor.js 公式纯函数层（解析/组行/KaTeX 校验/预览）` |
| 5 | `feat: 公式块就地编辑（编辑态 + 保存 + 校验拦截 + 对齐）` |
| 6 | `feat: 表格内公式弹窗（工具条按钮 + 插入/替换选中格）` |
| 7 | `test: f17 公式探针组（15 条）` |
| 8 | `feat: text-search.js 共享搜索层 + .search-highlight 样式` |
| 9 | `feat: 块页跨块搜索（计数 + 高亮 + 上一下一）` |
| 10 | `feat: 跨块替换（当前/全部，表格走 DOM 文本节点，公式非法则跳过）` |
| 11 | `test: f18 跨块搜索探针组（15 条）` |
| 12 | `docs: P4 公式 + 跨块搜索交付归档（spec §8.5/§12/§16/§18 + TODOS）` |
