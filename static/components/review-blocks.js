/* OCR 审查页（版面块 · P2 行级编辑）
 *
 * 依赖：md-render.js —— 块内容统一走 window.mdRender.renderHtml，
 *       切勿另起 marked/DOMPurify 内联渲染路径（本仓既定约定）。
 * 后端契约：
 *   GET  /import/review/{task_id}/layout              → {blocks, pages, stats, editable_labels}
 *   GET  /import/review/{task_id}/page/{n}.png?scale=  → image/png
 *   GET  /import/review/{task_id}/content             → {content, file_name}
 *   POST /import/review/{task_id}/compose  {edits:{下标:内容}} → {md_text}
 *   POST /import/review/{task_id}/preview  同上 → {count, clauses:[{clause_no,title}]}
 *   POST /import/review/{task_id}/confirm  **raw body**（text/plain）→ HTML 进度片段
 * 编辑**不落库**：「保存」只写 this.edits（spec §8.1）。
 *
 * 提交链路：compose（拼装，规则只此一份）→ confirm（既有 raw body 通道，端点零改动）。
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
        cutOpen: true,         // 第三栏（切分预览）折叠态；窄屏可收起让出宽度
        error: '',
        sessionExpired: false, // 登录失效（拿到 HTML 而非 JSON）时置真，页面顶部给出重新登录出口
        pageImgErr: false,     // 左栏页图加载失败（同上：多半是登录失效拿到 HTML）
        editableLabels: [],    // 后端下发的可编辑 label 白名单（单一来源，不在此另写一份）
        notInMarkdown: [],     // 后端下发的「不进 md 的 label」名单（同样单一来源；跨块搜索的排除判据）
        // 纯图块（OCR 不给 content，只给 page+bbox）⇒ 由后端按 bbox 裁页图来显示。
        // 与后端 `layout.py` 的 `not_in_markdown` 里那三个 `*_image` 对应；**只影响显示**。
        imageLabels: ['image', 'header_image', 'footer_image'],
        editingIdx: -1,        // 正在编辑的块下标（-1 = 无）
        edits: {},             // {块下标: 编辑后的 md}；编辑**不落库**，只存内存
        focusIdx: -1,          // 当前聚焦的**行下标**（-1 = 还没落到任何一行）
        focusIsClause: false,  // 当前行是否编号/标题行（响应式，供对齐禁用判据读）
        baseline: null,        // 首次 preview 响应的条文序列（= 导入前的切分基线）；null = 尚未取到
        preview: { count: 0, clauses: [], added: [], removed: [] },
        previewBusy: false,
        previewErr: '',
        submitting: false,     // 提交中（防重复点击；也给预览刷新守卫用）
        submitError: '',       // 提交失败的可见文案
        tableError: '',            // 表格编辑态的**块内**错误提示（不要复用页面级 submitError）
        _tableSnapshots: [],       // 结构操作的快照栈（Task 5 的撤销用它）
        _selAnchor: null,          // 选区锚点格（DOM 元素，不进响应式；Task 5 用）
        _previewTimer: null,
        _dragging: false,
        _dragFrom: null,
        // ── 拖框取图（P4 原型）──────────────────────────────────────────
        // **显式模式**：与「拖动查看」共用同一个 mousedown ⇒ 进模式后 startPan 让位。
        // 选显式模式而非修饰键，是因为**调框**（拖整体 / 拉边角）需要一个
        // 「不按任何键也能自由拖」的干净窗口（见 spec §8.4.1 的步骤表）。
        pickMode: false,
        pickRect: null,       // 画布内 CSS 像素，**原点 = 页图左上角**（与 crop 端点的栅格坐标同源）
        pickPreview: '',      // 右键确认后裁出的图 URL（原型：只预览，不落盘不插入）
        pickMsg: '',          // 模式内提示（框太小 / 页图尺寸未知 / 取图失败）
        _pickDrag: null,
        _pickSeq: 0,          // 取图请求序号：连点两次时只认最后一次的结果（初值必须给，否则 NaN）
        imgError: '',         // 图片块编辑态的**块内**错误提示（与 tableError 同口径）
        _imgBaseline: '',     // 进图片编辑态时的宿主快照，用于判「到底改没改」
        // ── 公式块编辑态（P4）──────────────────────────────────────────
        formulaError: '',     // 公式块编辑态的**块内**错误提示（与 tableError/imgError 同口径）
        formulaNote: '',      // 降级说明（`display_formula` 下不是标准公式行时可见）
        _fmlBaseline: null,   // 进公式编辑态时的 tex 快照，用于判「到底改没改」
        _fmlAlign: null,      // 公式对齐（'center'|'right'|null）；**保存时统一落**，与文本行一致
        _fmlAlignBaseline: null, // 进编辑态时**恢复出来的**对齐；守卫比的是它与 `_fmlAlign`
        _fmlSrcLine: null,    // 块 md 原文整行（`buildLine` 用它保留首尾空白）
        _fmlDisplay: true,    // 公式是行间($$)还是行内($)：标准公式行取 `p.display`，降级取 true
        // ── 表格内公式弹窗（P4 Task 5）：**页面级单例**（挂在 `x-for` 之外）──────
        // ⚠️ 这几项**只许**被根层/弹窗自身的绑定读；**不得**写进 `x-for` 里的块绑定
        //    （块内读页面级状态 ⇒ 每次交互跑 N 遍，本仓性能铁律）。
        fmlModalOpen: false,   // 弹窗存在性（模板用 `x-if`，不是 `x-show`）
        fmlModalTex: '',       // 弹窗输入框内容（公式本体，不含定界符）
        fmlModalDisplay: false, // false = 行内 `$…$`；true = 块级 `$$…$$`
        fmlModalError: '',     // 弹窗内的错误行（**只由「确定」写**；实时渲染错误走预览宿主）
        fmlModalCell: null,    // 打开时的目标格（DOM 元素，**不进响应式**：同 `_cropImg`）
        _fmlCaretAt: null,     // 打开时的光标位置（用**文本偏移**存，不存 Range 对象）
        cropMode: false,      // 裁剪模式（在编辑态里的图上再拖一个要保留的矩形）
        cropRect: null,       // 裁剪框：**显示坐标**（CSS 像素，原点 = 图左上角）
        _cropDrag: null,
        _cropImg: null,       // 加进裁剪模式的那张 `<img>`（DOM 元素**不进响应式**）
        // ── P4 Task 9：跨块搜索（工具条 + 计数 + DOM 高亮；替换/跳转是 Task 10）──────
        // ⚠️ 这三项**只许**被工具条那一层（`x-for` **之外**）的绑定读：
        //    块内读页面级状态 ⇒ 每次交互在 4000 块上各跑一遍（本仓性能铁律，记过 857ms 的事故）。
        searchQuery: '',       // 搜索词（绑在工具条输入框上）
        searchHits: [],        // 命中块：[{blockIdx, __i, count}]（字符串域计数，见 findMatches）
        searchIdx: -1,         // 当前项下标（Task 10 的上一处/下一处用；本 Task 只维护不消费）
        searchInfo: '',        // 计数字样「N 个匹配 · M 个可替换」
        searchMsg: '',         // `M < N` 时的原因说明（挂 `title`，D2 的诚实口径）
        _searchTimer: null,    // 搜索防抖（与 `_previewTimer` 分开，勿共用一个句柄）


        async init() {
            this.taskId = window.location.pathname.split('/').pop();
            let data;
            try {
                const resp = await fetch(`/import/review/${this.taskId}/layout`);
                if (!resp.ok) {
                    // 无 layout（老任务 / .md / 非扫描件）⇒ 明确告知并给出出口
                    this.error = '该任务没有版面数据（可能不是扫描件，或用了不产生版面信息的 OCR 后端）。';
                    return;
                }
                data = await this._json(resp);
            } catch (e) {
                this.error = '版面数据加载失败：' + e;
                return;
            }
            this.pages = data.pages || [];
            this.editableLabels = data.editable_labels || [];
            this.notInMarkdown = data.not_in_markdown || [];
            // __i = 原下标：过滤/排序后仍能定位回 blocks（联动与 P2 的 edits 都用它）
            this.blocks = (data.blocks || []).map((b, i) => Object.assign({}, b, { __i: i }));
            this.curPage = this.pages.length ? this.pages[0].index : 1;
            this.fileName = await this._fileName();
            this.$nextTick(() => this._applyFit());
            // 切分预览基线：此刻 edits 为空 ⇒ 首次响应即「导入前的切分」（spec §11.4）
            this.refreshPreview();
        },

        async _fileName() {
            try {
                const r = await fetch(`/import/review/${this.taskId}/content`);
                if (!r.ok) return '';
                return ((await this._json(r)).file_name || '');
            } catch (e) {
                return '';
            }
        },

        /** 把响应当 JSON 读；**不是 JSON 就翻成人话**。
         *
         * ⚠️ 为什么必须单独识别（用户实测踩过）：登录失效后 `AuthMiddleware` 是 `302 → /login`，
         *    `fetch` **静默跟随** ⇒ 这里拿到的是**登录页 HTML**，而且 **HTTP 状态是 200**、
         *    `resp.ok` 为真。于是直接 `resp.json()` 抛的是
         *    `SyntaxError: Unexpected token '<', "<!DOCTYPE "... is not valid JSON`
         *    —— 用户只看到这一句，既不知道是登录过期、也没有任何自救出口。
         *    同一时刻 `<img src=/page/N.png>` 也拿到 HTML ⇒ 左栏变成破图。
         *    ⇒ 统一在这里识别，置 `sessionExpired` 让页面顶部给出重新登录的链接。 */
        async _json(resp) {
            const ct = (resp.headers.get('content-type') || '').toLowerCase();
            if (!ct.includes('json')) {
                this.sessionExpired = true;
                throw new Error('登录已过期（拿到的是登录页而不是数据）');
            }
            return await resp.json();
        },

        /** 已锚定 = 有行区间 ⇒ 可在原文件上定位、可画框 */
        hasSpan(b) {
            return b.start_line !== null && b.start_line !== undefined;
        },

        blocksOnPage() {
            return this.blocks.filter((b) => b.page === this.curPage);
        },

        /** 请求倍率 = CSS 倍率 × devicePixelRatio，**并夹到端点上限 8.0**。
         *
         * ⚠️ 不乘 dpr 时：PNG 像素宽 ≡ 容器 CSS 宽（与单位无关地恒等）⇒ 恒为
         * 「1 图像像素 : 1 CSS 像素」。在 200% 显示缩放的屏幕上，浏览器要 2 个
         * 设备像素而位图只有 1 个 ⇒ 放大插值 ⇒ 糊。**画布 CSS 宽不用改**：
         * `.rvb-img{width:100%}` 会把多出来的像素正确地缩回去。
         *
         * ⚠️ **上限 8.0 与端点 `app/routes/import_routes.py` 的 `scale` 上限必须同步**
         * （前端无法 import 后端常量，故此处硬编码，改一处须改另一处）。`_fitScale()`
         * 顶格 3.0、`scaleMode` 最大 2.0 ⇒ 乘机会超过 8.0（如 dpr > 2.67 的高分屏 +
         * 窄页吃满 fit 档）；不夹则端点返回 422、左栏**整页图加载失败**（比模糊更糟）。
         */
        pageUrl(n) {
            const css = this.scaleMode === 'fit' ? this._fitScale() : this.scaleMode;
            const s = Math.min(8.0, css * (window.devicePixelRatio || 1));
            return `/import/review/${this.taskId}/page/${n}.png?scale=${Number(s).toFixed(2)}`;
        },

        /** 「适应宽度」的倍数 = 容器宽 ÷ 页宽（页宽来自 layout.json.pages） */
        _fitScale() {
            const vp = this.$refs.vp;
            const p = this.pages.find((x) => x.index === this.curPage);
            if (!vp || !p || !p.width) return 1;
            return Math.max(0.25, Math.min(3.0, vp.clientWidth / p.width));
        },

        _applyFit() {
            if (this.scaleMode === 'fit') this.zoom = this._fitScale();
        },

        setScaleMode(m) {
            this.scaleMode = m;
            this.zoom = (m === 'fit') ? this._fitScale() : m;
            // 选框存的是**像素**（与 boxStyle 的百分比方案不同）⇒ 缩放档一变就失效。
            // 与其让它挂在一个错的位置上，不如清掉：宁可重框一次，不可错裁一张。
            this.pickRect = null;
            this.pickPreview = '';
            this.$nextTick(() => this._applyFit());
        },

        /** 「重置」= 回到「适应宽度」（spec §6.2：**不是刷新**） */
        resetZoom() {
            this.setScaleMode('fit');
        },

        canvasStyle() {
            const p = this.pages.find((x) => x.index === this.curPage);
            const w = (p && p.width) ? p.width * (this.zoom || 1) : null;
            return w ? `width:${w}px` : '';
        },

        onImgLoad() {
            this._applyFit();     // 图加载后布局可能变，fit 档重算一次
        },

        /** 高亮框：bbox 按**百分比**定位 ⇒ 缩放自动跟随，无需重算（spec §6.3） */
        boxStyle(b) {
            const p = this.pages.find((x) => x.index === b.page);
            if (!p || !p.width || !p.height || !Array.isArray(b.bbox)) return 'display:none';
            const x0 = b.bbox[0], y0 = b.bbox[1], x1 = b.bbox[2], y1 = b.bbox[3];
            return `left:${(x0 / p.width) * 100}%;top:${(y0 / p.height) * 100}%;`
                 + `width:${((x1 - x0) / p.width) * 100}%;`
                 + `height:${((y1 - y0) / p.height) * 100}%`;
        },

        /** 放大后拖动查看被遮盖内容：**只在无版面块处**触发（块上的点击留给联动） */
        startPan(e) {
            if (this.pickMode) return;          // 取图模式下让位给画框（两者共用一个 mousedown）
            if (e.target.closest('.rvb-box')) return;
            const vp = this.$refs.vp;
            if (!vp) return;
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

        /** 进入 / 退出「取图」模式。进出都清空选框与预览 —— 否则下次进来还挂着上次那个框。 */
        togglePickMode() {
            this.pickMode = !this.pickMode;
            this.pickRect = null;
            this.pickPreview = '';
            this.pickMsg = '';
        },

        /** 选框样式：`pickRect` 与画布同单位（内 CSS 像素）⇒ 直接按像素定位。
         *  ⚠️ 与 `boxStyle()` 的百分比方案不同：这里存**像素**，故缩放档一变就失效
         *     （`setScaleMode` 会清空选框，而不是让它挂在错的位置上）。 */
        selStyle() {
            const r = this.pickRect;
            if (!r) return '';
            return `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px`;
        },

        /** 取图模式下的 mousedown：命中手柄 → 调框；框内 → 整体拖；框外 → 起新框。
         *
         *  命中判定走 `imagePicker.hitHandle` 的**坐标计算**，不依赖手柄 DOM
         *  ⇒ 手柄可以整层 `pointer-events:none`，只作视觉提示。 */
        onPickDown(e) {
            if (!this.pickMode || e.button !== 0) return;
            const img = this.$refs.pgimg;
            if (!img) return;
            e.preventDefault();
            this.pickMsg = '';
            // 坐标基准 = **页图**的边界框（不是画布）：页图就是页面栅格的等比缩放，
            // 用同一个 `box` 既量坐标又算换算，避免两处取整不一致。
            const box = img.getBoundingClientRect();
            const px = e.clientX - box.left, py = e.clientY - box.top;
            const ip = window.imagePicker;
            const handle = this.pickRect ? ip.hitHandle(this.pickRect, px, py, ip.HANDLE_TOL) : null;
            if (handle === 'move') {
                this._pickDrag = { mode: 'move', x0: px, y0: py,
                                   orig: { x: this.pickRect.x, y: this.pickRect.y,
                                           w: this.pickRect.w, h: this.pickRect.h } };
            } else if (handle) {
                this._pickDrag = { mode: 'resize', handle: handle };
            } else {
                this.pickRect = ip.normalize(px, py, px, py);
                this._pickDrag = { mode: 'new', x0: px, y0: py };
            }
            const move = (ev) => this._pickMove(ev, box);
            const up = () => {
                this._pickDrag = null;
                window.removeEventListener('mousemove', move);
                window.removeEventListener('mouseup', up);
                if (this.pickRect) {
                    this.pickRect = window.imagePicker.clampRect(this.pickRect, box.width, box.height);
                    // 碎拖丢弃：留一个 1×1 的框只会让「确认」送出一个无意义的裁切请求
                    if (!window.imagePicker.isUsable(this.pickRect)) this.pickRect = null;
                }
            };
            window.addEventListener('mousemove', move);
            window.addEventListener('mouseup', up);
        },

        _pickMove(ev, box) {
            const d = this._pickDrag;
            if (!d) return;
            const ip = window.imagePicker;
            const px = ev.clientX - box.left, py = ev.clientY - box.top;
            if (d.mode === 'new') {
                this.pickRect = ip.normalize(d.x0, d.y0, px, py);
            } else if (d.mode === 'resize') {
                this.pickRect = ip.resize(this.pickRect, d.handle, px, py);
            } else {
                // 整体平移：夹的是**位移**而不是矩形 —— 夹矩形会在贴边时把框压小
                const x = Math.max(0, Math.min(d.orig.x + (px - d.x0), box.width - d.orig.w));
                const y = Math.max(0, Math.min(d.orig.y + (py - d.y0), box.height - d.orig.h));
                this.pickRect = { x: x, y: y, w: d.orig.w, h: d.orig.h };
            }
            this.pickRect = window.imagePicker.clampRect(this.pickRect, box.width, box.height);
        },

        /** 右键确认：把选框换算成 OCR 页面栅格坐标，调 **P3 已交付**的裁切端点取图。
         *
         *  原型阶段**只预览**：不落盘、不插入单元格（那是 P4 的活）。这一步真正的价值
         *  是把几何**验穿** —— 裁出来正是圈中的那块，才说明换算正确。
         *
         *  ⚠️ 非模式时**不** preventDefault：右键该照常弹浏览器菜单。 */
        confirmPick(e) {
            if (!this.pickMode) return;
            e.preventDefault();
            // ★ 先解析**落点**：没有编辑目标就**不取图、不上传**，直接告诉用户下一步。
            //   顺序不能颠倒 —— 先抓图再发现没地方放，用户白等一次网络往返，而且文件
            //   已经落进 `imgs/` 了（留下一个没人引用的垃圾文件）。
            const target = this._pickTarget();
            if (target.kind === 'none') {
                this.pickMsg = '请先点某个块的「纠正」进入编辑态，再框选取图';
                return;
            }
            if (target.kind === 'text') {
                this.pickMsg = '文本块不支持插图，请改为在表格单元格或图片块编辑态下取图';
                return;
            }
            if (!this.pickRect) { this.pickMsg = '请先在页图上拖出一个框'; return; }
            const img = this.$refs.pgimg;
            const p = this.pages.find((x) => x.index === this.curPage);
            if (!img || !p || !p.width || !p.height) { this.pickMsg = '页图尺寸未知，无法取图'; return; }
            const box = img.getBoundingClientRect();
            const r = window.imagePicker.clampRect(this.pickRect, box.width, box.height);
            if (!window.imagePicker.isUsable(r)) { this.pickMsg = '选框太小，请重新框选'; return; }
            const g = window.imagePicker.toGrid(r, box.width, box.height, p.width, p.height);
            if (!g) { this.pickMsg = '页图尺寸未知，无法取图'; return; }
            this._pickAndInsert(`/import/review/${this.taskId}/crop/${this.curPage}.png`
                + `?x=${g.x.toFixed(1)}&y=${g.y.toFixed(1)}&w=${g.w.toFixed(1)}`
                + `&h=${g.h.toFixed(1)}&scale=1.5`, target);
        },

        /** 取图的**落点**。四种结果：cell（表格格）/ image（图片块）/ text（不支持）/ none。
         *
         *  ⚠️ 表格的「当前格」口径 = 既有惯用式（第一个 `.rvb-cell-sel`，回落首个 `td,th`），
         *  **不是** `_selAnchor` —— 那是 Shift 扩选的锚点，语义不同（`tableOp` 等三处同口径）。 */
        _pickTarget() {
            if (this.editingIdx < 0) return { kind: 'none' };
            const b = this.blocks[this.editingIdx];
            if (!b) return { kind: 'none' };
            if (this.imageLabels.indexOf(b.label) >= 0) {
                const host = this._imgHost();
                return host ? { kind: 'image', host: host } : { kind: 'none' };
            }
            if (b.label === 'table') {
                const t = this._tableEl();
                if (!t) return { kind: 'none' };
                const cell = t.querySelector('.rvb-cell-sel') || t.querySelector('td,th');
                return cell ? { kind: 'cell', cell: cell } : { kind: 'none' };
            }
            return { kind: 'text' };
        },

        /** 把取到（并已落盘）的图放进落点。返回是否成功。
         *
         *  ⚠️ 传进来的 `url` 是**绝对**路径（编辑态要立即可见）；相对化发生在保存时。 */
        _insertInto(target, url) {
            if (target.kind === 'image') return this._putImgInHost(target.host, url);
            if (target.kind === 'cell') {
                const img = document.createElement('img');
                img.setAttribute('src', url);
                img.setAttribute('alt', 'Image');
                target.cell.appendChild(img);
                return true;
            }
            return false;
        },

        /** 取图 → **落盘** → 插入落点。先取一张当预览（用户要能看见自己圈的是哪块），
         *  再上传、再插入；两步各自失败都给出可读原因（裁切失败=任务/页面问题，上传失败=落盘问题）。
         *
         * ⚠️ 为什么自己 fetch 而不是直接把 URL 塞给 `<img>`（2026-10-07 事故）：
         *    `<img>` 加载失败**不给你任何错误信息** —— 只剩一个破图占位、`pickMsg` 也是空的，
         *    用户只能得出「功能坏了」。真实原因是任务被 TTL 清扫器删掉后 crop 端点返回
         *    `404 {"detail":"该页没有版面尺寸"}`；自己 fetch 就能拿到状态码与后端给的原因。
         *
         * ⚠️ 拿到的必须是 **Blob 再 POST 给落盘端点**：这样三入口（粘贴 / 拖文件 / 取图）
         *    汇到同一个落点、共用同一套校验（spec §8.4）。 */
        async _pickAndInsert(url, target) {
            const seq = ++this._pickSeq;     // 连点两次时，只认最后一次的结果
            this.pickPreview = '';           // 先撤掉旧图：留着会与「失败」提示自相矛盾
            this.pickMsg = '';
            try {
                const resp = await fetch(url);
                if (seq !== this._pickSeq) return;
                if (!resp.ok) {
                    let detail = '';
                    try { detail = ((await resp.json()) || {}).detail || ''; } catch (e) { detail = ''; }
                    this.pickMsg = `取图失败（HTTP ${resp.status}）` + (detail ? `：${detail}` : '');
                    return;
                }
                if (!(resp.headers.get('content-type') || '').includes('image')) {
                    // 登录失效时后端 `302 → /login` 且 fetch 静默跟随 ⇒ 拿到 200 + HTML
                    this.sessionExpired = true;
                    this.pickMsg = '取图失败：登录已过期，请重新登录后再试';
                    return;
                }
                const blob = await resp.blob();          // ← 落盘要有字节
                if (seq !== this._pickSeq) return;
                this.pickPreview = url;                  // 视觉确认：圈中的就是这块
                const filename = await this._uploadImage(blob);
                if (seq !== this._pickSeq) return;
                if (!this._insertInto(target, this.imgAbsBase() + filename)) {
                    this.pickMsg = '取到了图，却没有可插入的位置（编辑态可能已经关掉了）';
                    return;
                }
                this.pickMsg = '已插入'
                    + (target.kind === 'cell' ? '当前单元格' : '当前图片块') + '，点「保存」生效';
            } catch (e) {
                if (seq !== this._pickSeq) return;
                this.pickMsg = '取图失败：' + e;
            }
        },

        /** 把一张图落到服务端（图片线的**共用落点**），返回**文件名**。
         *
         *  图片三入口（点击选文件 / 拖拽 / 粘贴）、裁剪另存、拖框取图**都走这里** ——
         *  「新增文件名、不覆盖」由后端保证（见 `POST /import/review/{id}/image`）。
         *  失败时**抛出**带可读原因的 Error，由调用方就近显示；不在这里吞。 */
        async _uploadImage(blob) {
            const resp = await fetch(`/import/review/${this.taskId}/image`, {
                method: 'POST',
                headers: { 'Content-Type': (blob && blob.type) || 'image/png' },
                body: blob,
            });
            if (!resp.ok) {
                let detail = '';
                try { detail = ((await resp.json()) || {}).detail || ''; } catch (e) { detail = ''; }
                throw new Error(`上传失败（HTTP ${resp.status}）` + (detail ? `：${detail}` : ''));
            }
            const data = await this._json(resp);       // 非 JSON ⇒ 置 sessionExpired 并抛
            if (!data || !data.filename) throw new Error('上传成功但没拿到文件名');
            return data.filename;
        },

        labelZh(label) {
            const m = {
                doc_title: '文档标题', paragraph_title: '段落标题', text: '正文',
                content: '正文', table: '表格', image: '图片', figure_title: '图题',
                display_formula: '公式', formula_number: '公式编号', chart: '图表',
                number: '页码', vision_footnote: '脚注',
            };
            return m[label] || label || '未知';
        },

        renderBlock(b) {
            try {
                // `image` 类块的 `content` 是**空串**（OCR 不下发图片本体，图只落在 markdown 里），
                // 而块自带 `page` + `bbox` ⇒ 交给后端**按 bbox 从该页裁**一张出来显示。
                // 实测（122 页文档）：132 个 image 块原本全是空白，中栏只剩 1 张表里的 13 张图能看见。
                const pic = this._cropUrl(b);
                if (pic) return `<img class="rvb-block-img" src="${pic}" alt="版面图块" loading="lazy">`;
                return window.mdRender.renderHtml(this.contentOf(b),
                    '/import/review/' + this.taskId + '/');
            } catch (e) {
                return '<p style="color:#c00">渲染错误</p>';
            }
        },

        /** `image` 类块的裁切图 URL；不是图块（或它自己有内容）则返回 `null`。
         *
         * ⚠️ URL 全部由**数值**与 `taskId`（后端已按 8 位十六进制校验）拼成 ⇒ 无注入面。
         * ⚠️ `bbox` 是 `[x0, y0, x1, y1]`（OCR 栅格坐标，左右/上下都可能反序）⇒ 必须归一化，
         *    否则后端拿到负宽高只能 422。 */
        _cropUrl(b) {
            if (!this.imageLabels.includes(b.label)) return null;
            // ★ **编辑过的块一律不回落 bbox 裁图** —— 判据不能只看「内容非空」：
            //   用户点「删除图片」后 `edits[i]` 是**空串**，按"内容非空"判的话空串会落回
            //   bbox 裁图 ⇒ **删了又冒出来**，用户以为删除失败。
            if (this.isEdited(b)) return null;
            if ((this.contentOf(b) || '').trim()) return null;   // 块自己有内容就按内容渲染
            const bb = b.bbox;
            if (!Array.isArray(bb) || bb.length !== 4) return null;
            const [ax, ay, bx, by] = bb.map(Number);
            if (![ax, ay, bx, by].every(Number.isFinite)) return null;
            const x0 = Math.min(ax, bx), y0 = Math.min(ay, by);
            const w = Math.abs(bx - ax), h = Math.abs(by - ay);
            if (w < 1 || h < 1) return null;
            const q = `x=${x0.toFixed(1)}&y=${y0.toFixed(1)}&w=${w.toFixed(1)}&h=${h.toFixed(1)}&scale=1.5`;
            return `/import/review/${this.taskId}/crop/${b.page}.png?${q}`;
        },

        /** ★ 双向联动：两侧定位到**同一块**、同一视觉样式（spec §9） */
        focusBlock(idx) {
            const b = this.blocks[idx];
            if (!b) return;
            this.activeIdx = idx;
            if (b.page && b.page !== this.curPage) this.curPage = b.page;   // 跨页自动翻
            this.$nextTick(() => {
                if (this.hasSpan(b)) this._scrollLeftTo();
                this._scrollRightTo(idx);
            });
        },

        /** 把「编辑态 / 高亮态」从**逐块 Alpine 绑定**改为**一处命令式同步**（性能必需）。
         *
         * ⚠️ **为什么不能用逐块绑定**：模板里每个块原本都读**页面级**的 `editingIdx` / `tableError`，
         *    而 Alpine 的规则是「谁读过、变量一变谁就重算」⇒ 改一次要跑 N 遍。
         *    实测（4000 块 / 284761 个 DOM 节点 / 28163 个 x-show 元素）：
         *      进入编辑 **857ms**、取消 **709ms**、单独写一次 `activeIdx` **93ms**，
         *      全部随块数**线性增长**；而写一个模板不读的字段只要 4ms。
         *      合并/撤销不动这几个变量 ⇒ 一直 70–90ms，刚好解释用户「进编辑和取消最卡」的观感。
         *    改成只动**两个**块之后，这几个数都掉到几十毫秒。
         *
         * ⚠️ **依赖靠"读"建立**：本方法由根元素上**唯一一个** `x-effect="syncEditingUi()"` 调用，
         *    开头那三行 `this.xxx` 的**读取**就是依赖声明。
         *    **不要把这三行挪进别的函数** —— 那样 `x-effect` 追不到依赖，界面就再也不更新了。
         */
        syncEditingUi() {
            const idx = this.editingIdx;          // ← 读（建立依赖），别动
            const act = this.activeIdx;           // ← 读（建立依赖），别动
            const err = this.tableError;          // ← 读（建立依赖），别动
            if (this._uiEditing !== idx) {
                const prev = this._blockEl(this._uiEditing);
                if (prev) {
                    prev.classList.remove('rvb-block--editing');
                    const pp = prev.querySelector('.rvb-table-err');
                    if (pp) pp.textContent = '';   // 旧块的块内报错随编辑态一起清掉
                }
                this._blockEl(idx)?.classList.add('rvb-block--editing');
                this._uiEditing = idx;
            }
            if (this._uiActive !== act) {
                this._blockEl(this._uiActive)?.classList.remove('rvb-block--on');
                this._blockEl(act)?.classList.add('rvb-block--on');
                this._uiActive = act;
            }
            // 表格错误只出现在**当前编辑块**里；`tableError` 变化时本 effect 同样会重跑
            const p = this._blockEl(idx)?.querySelector('.rvb-table-err');
            if (p) p.textContent = err || '';
        },

        /** 按块下标取块元素（下标为 `-1`/非数字时返回 `null`） */
        _blockEl(idx) {
            return (typeof idx === 'number' && idx >= 0)
                ? document.querySelector(`[data-block-idx="${idx}"]`) : null;
        },

        /** 让 `el` 在 `box` **这一个**容器内就位，**不牵动任何外层滚动容器**。
         *
         * ⚠️ **不能改用 `scrollIntoView`**：它会连**所有可滚动祖先**一起滚 —— 外层
         * `.center-panel-v2` 自三栏占满一屏后也可滚动，于是点一下切分栏的条号，
         * 整页跟着跳（用户实测）。手动改 `scrollTop` 只影响指定容器。
         * `block`：`'center'` 居中；`'nearest'` 贴近边缘（已在视内则不动）。
         */
        _scrollInto(el, box, block) {
            if (!el || !box) return;
            const er = el.getBoundingClientRect();
            const br = box.getBoundingClientRect();
            if (block === 'center') {
                box.scrollTop += (er.top - br.top) - (box.clientHeight - er.height) / 2;
            } else if (block === 'start') {
                box.scrollTop += er.top - br.top;      // 把元素**顶边**对齐容器顶边
            } else if (er.top < br.top) {
                box.scrollTop += er.top - br.top;
            } else if (er.bottom > br.bottom) {
                box.scrollTop += er.bottom - br.bottom;
            }
        },

        _scrollLeftTo() {
            const vp = this.$refs.vp;
            this._scrollInto(vp && vp.querySelector('.rvb-box--on'), vp, 'center');
        },

        _scrollRightTo(idx, mode) {
            const list = this.$refs.list;
            this._scrollInto(list && list.querySelector(`[data-block-idx="${idx}"]`),
                             list, mode || 'nearest');
        },

        // ── P2：行级编辑（编辑**只写内存** this.edits，不发任何写请求） ──

        /** 可编辑 = **有行区间**（能安全回写）且在 label 白名单内（后端下发） */
        canEdit(b) {
            return this.hasSpan(b) && this.editableLabels.indexOf(b.label) >= 0;
        },

        // ── P4 图片线：图片块的编辑态 ────────────────────────────────

        /** 图片块可编辑 = **有行区间**（能安全回写）且是图片类 label。
         *
         *  为什么与 `canEdit` 分开：图片块的 `content` 是**空串**、也不在
         *  `EDITABLE_TEXT_LABELS` 里；但后端 `compose` 的写入守卫**只查 `start_line`**
         *  （不查 label 白名单），`splice` 也只枚举有 span 的块 ⇒ 只要锚上了就能安全回写。
         *  没锚上就绝不能进编辑态（那样保存会被 422 拒）。 */
        canEditImage(b) {
            return !!b && this.hasSpan(b) && this.imageLabels.indexOf(b.label) >= 0;
        },

        /** 编辑态里图片 src 的**绝对前缀**。
         *
         *  ⚠️ 必需：页 URL 是 `/import/review/{taskId}`（**无尾斜杠**），相对 `imgs/…`
         *  会解析成 `/import/review/imgs/…` ⇒ 404。只读态靠 `mdRender.rewriteImg` 修，
         *  编辑态**没有**那一层 ⇒ 表内/图块内的图在编辑态本来全是破图。 */
        imgAbsBase() {
            return `/import/review/${this.taskId}/imgs/`;
        },

        /** 进编辑态：相对 → 绝对（幂等：已经是绝对的再调是 no-op） */
        _absImgs(html) {
            return String(html == null ? '' : html)
                .split('src="imgs/').join('src="' + this.imgAbsBase());
        },

        /** 表格编辑态：把表内的 `imgs/…` 就地绝对化，**并重取 `_rvbBaseline`**。
         *
         *  ⚠️ 两步缺一不可：
         *  ① 不绝对化 ⇒ 相对路径按页 URL（`/import/review/{tid}`，无尾斜杠）解析成
         *     `/import/review/imgs/…` ⇒ **编辑态里表内所有既有的图都是破图**（既有缺陷）。
         *  ② 不重取 baseline ⇒ `tableEditor.serialize` 的 `html === _rvbBaseline` 判据失效
         *     （基线是 parse 时的相对形态）⇒ **用户什么都没改，保存一次也会把 md
         *     从原始串改写成归一化 HTML**（R1：逐字节往返被打破）。
         *  且重取必须发生在**挂 `contenteditable` / 打选中态之前** —— 否则基线里会混进
         *  编辑器痕迹，`_scrub` 一剥就永远对不上（等于没重取）。 */
        _absTableImgs(t) {
            for (const im of t.querySelectorAll('img')) {
                const s = im.getAttribute('src') || '';
                if (s.indexOf('imgs/') === 0) im.setAttribute('src', this.imgAbsBase() + s.slice(5));
            }
            t._rvbBaseline = t.outerHTML;
        },

        /** 保存时：绝对 → 相对。md 里必须存**相对**引用才可移植
         *  （长期目录与分发包都按 `imgs/…` 解析）。 */
        _relImgs(html) {
            return String(html == null ? '' : html)
                .split('src="' + this.imgAbsBase()).join('src="imgs/');
        },

        /** 图片块的当前 md 原文：有编辑用编辑，否则用 layout 下发的 `md_html`。
         *  ⚠️ **不能**回落 `b.content` —— 图片块的 content 是空串。 */
        _imgSource(b) {
            return this.edits[b.__i] ?? b.md_html ?? '';
        },

        /** 图片编辑宿主（同样**按块下标**取 —— `x-ref` 在 `x-for` 里会被最后一项覆盖） */
        _imgHost() {
            const list = this.$refs.list;
            return list
                ? list.querySelector(`[data-block-idx="${this.editingIdx}"] .rvb-img-edit`)
                : null;
        },

        /** 构建图片块编辑器：宿主里放**整行 HTML**（含对齐包裹等），src 绝对化。
         *
         *  为什么放整行而不是只放 `<img>`：对齐包裹与 `width`/`alt` 都长在这一行上，
         *  只放 `<img>` 会在保存时把它们全丢掉（用户没动过对齐，保存一次却没了）。 */
        _buildImgEditor(b) {
            const host = this._imgHost();
            if (!host) return;
            const src = this._imgSource(b);
            host.innerHTML = src.trim()
                ? this._absImgs(src)
                : '<p class="rvb-img-empty">此图块当前没有图片引用</p>';
            // 进编辑态时的快照 —— 保存时据此判「用户到底改没改」。
            // ⚠️ 不能直接拿 `_relImgs(host.innerHTML)` 与源串比：innerHTML 会把
            //    `<img … />` 归一成 `<img …>`、属性顺序也可能变 ⇒ 未改动也会判成"改了"，
            //    于是凭空写一条 edits ⇒ 只读态的 bbox 裁图被 edited-guard 挡掉。
            this._imgBaseline = host.innerHTML;
        },

        // ── P4 公式线：公式块的编辑态（独立分支 + 独立宿主，**不落行编辑器**）──────

        /** 公式编辑部件宿主（同样**按块下标**取 —— `x-ref` 在 `x-for` 里会被最后一项覆盖） */
        _fmlHost() {
            const list = this.$refs.list;
            return list
                ? list.querySelector(`[data-block-idx="${this.editingIdx}"] .rvb-fml-wrap`)
                : null;
        },

        /** 公式编辑框（textarea）。编辑源由 JS 管：进编辑态 `.value =` 灌入，保存时读回。 */
        _fmlSrc() {
            const host = this._fmlHost();
            return host ? host.querySelector('.rvb-fml-src') : null;
        },

        _fmlPreviewHost() {
            const host = this._fmlHost();
            return host ? host.querySelector('.rvb-fml-preview') : null;
        },

        /** 构建公式块编辑器：把**公式本体**（tex）灌进 textarea 并渲染一次预览。
         *
         *  ⚠️ **必须先剥掉对齐包裹**（评审 Finding 1，`纠正 → 中 → 保存 → 再纠正`）：
         *    保存后 `edits[i]` 是 `mdInline.wrap(line, align)` 的产物 =
         *    `<span style="display:block;text-align:X"> $$…$$ </span>`；而 `contentOf` 一旦
         *    `edits[i]` 存在就优先返回它。`formulaEditor.parseLine` 是**整行锚定**、要求
         *    定界符之外只有空白 ⇒ 不剥包裹就判 `null` ⇒ 误命中降级分支（编辑框装进原始
         *    span HTML、谎报「不是标准公式行」），且**保存时对齐被静默丢掉**。
         *    文本行走的是同一路（`_makeLine` 用 `mdInline.parseLine` 恢复 `el.dataset.align`）
         *    —— 公式这条路径此前缺了同样的恢复，属**不对称**缺陷。
         *  ⚠️ 恢复出的对齐写进 `_fmlAlign`，但守卫比的是它与 `_fmlAlignBaseline`
         *    （**不能**靠 `_fmlAlign === null` 判「没改过」：那时「已对齐但没改公式」会被
         *    误判成改过 ⇒ 凭空写一条 edits）。
         *  ⚠️ **降级分支不可省**：`display_formula` 标签下**并非一定是公式行**（实测有若干例
         *    来自合成块）⇒ 剥包裹后 `parseLine` 仍可能返回 `null`。此时把整行内容当公式本体
         *    让用户能修，**绝不**直接取 `p.tex` 而抛；并在块内可见地说明「这不是标准公式行」。
         *  ⚠️ `_fmlSrcLine` 存的是**剥包裹后的那一行**（含首尾空白）—— `buildLine` 靠它把
         *    ` $$ … $$ ` 前后的空格逐字节还回去（真实夹具 21/21 都是这种形态）。 */
        _buildFormulaEditor(b) {
            const al = window.mdInline.parseLine(this.contentOf(b));
            const src = al.inner;              // ← 剥掉外层对齐包裹（无包裹时 = 原文）
            const p = window.formulaEditor.parseLine(src);
            let tex, display, note;
            if (p) {
                tex = p.tex; display = p.display; note = '';
            } else {
                tex = src; display = true; note = '这一块不是标准的公式行，已按整行内容编辑';
            }
            this._fmlBaseline = tex;
            this._fmlSrcLine = src;
            this._fmlAlign = al.align;              // 恢复出的对齐（重进编辑态即带上）
            this._fmlAlignBaseline = al.align;      // 基线：守卫比的是「对齐有没有被动过」
            this._fmlDisplay = display;
            this.formulaError = '';
            this.formulaNote = note;
            const box = this._fmlSrc();
            if (box) box.value = tex;
            this.formulaPreview();          // 进编辑态立刻渲染一次（用户要能看见当前公式）
        },

        /** 实时校验 + 预览：读 textarea 的**当前值**（编辑源在 DOM，不在响应式状态里）。
         *  非法时把错误写进 `formulaError`（并在预览宿主里也显示错误），合法则清空。 */
        formulaPreview() {
            const box = this._fmlSrc();
            if (!box) return;
            const tex = box.value;
            const display = this._fmlDisplay;
            const r = window.formulaEditor.validate(tex, display);
            this.formulaError = r.ok ? '' : r.error;
            window.formulaEditor.renderPreview(this._fmlPreviewHost(), tex, display);
        },

        /** 公式对齐：**只记，不写行**（保存时统一落 —— 与文本行「保存时统一落」口径一致）。 */
        formulaAlign(v) {
            this._fmlAlign = v;
        },

        // ── P4 公式线：**表格内**公式弹窗（页面级**单例**，D1 = 方案 A）──────────
        //
        // 与公式块（上面那节）的分工：公式块是「一块 = 一行公式」的就地编辑；这里是
        // 「表格某个**格**里的一段 `$…$`」的插入/替换 —— 落点是 `contenteditable` 的格。
        //
        // ★ 前提已**实测**（2026-10-07，Task 5 第一步）：表格编辑态里格内是**字面 `$…$`**，
        //   不是 `.katex` —— `enterEdit` 的表格分支把 **md 原文** `md_html` 交给
        //   `window.tableEditor.parse`（`box.innerHTML = src`，全程不经 KaTeX）。
        //   真实产物 `7c5b6ac9` 的 table 块 md_html（30 个 `$`）走真实 UI 进编辑态实测：
        //   `{katexInEditor: 0, katexInCells: 0, cells: 32, cellsWithDollar: 10}`。
        //   ⇒ 预填**从格内文本读**（`formulaEditor.findInText`），不必解 MathML。
        //
        // ⚠️ 定位**一律用 `formulaEditor.findInText`**，不在这里另写一套 `$…$` 扫描：
        //   `$$…$$` 优先、空公式不选、区间含定界符，这三条口径只该有一份实现。
        // ⚠️ 写入**不序列化、不写 `edits`** —— 表格仍由用户点「保存」走 `saveEdit` 的表格分支
        //   落库（与「编辑期只改活 DOM、保存才落 edits」的既有语义一致）。

        /** 弹窗预览宿主 / 输入框：弹窗是页面级**单例**（不在 `x-for` 内）⇒ 按文档查是安全的
         *  （`x-for` 里同名 `x-ref` 会被最后一项覆盖，那是另一条坑，见 `_editBox`）。 */
        _fmlModalInput() {
            return document.querySelector('.rvb-fml-modal .rvb-fml-modal-src');
        },

        _fmlModalPreviewHost() {
            return document.querySelector('.rvb-fml-modal .rvb-fml-modal-preview');
        },

        /** 弹窗标题：写明作用在**哪一格**（用户要知道自己在改哪）。 */
        fmlModalTitle() {
            const cell = this.fmlModalCell;
            const t = this._tableEl();
            if (!cell || !t) return '表格公式';
            const rows = [...t.querySelectorAll('tr')];
            const tr = cell.closest('tr');
            const r = rows.indexOf(tr) + 1;
            const c = tr ? [...tr.children].filter((x) => /^(TD|TH)$/i.test(x.tagName)).indexOf(cell) + 1 : 0;
            return (r > 0 && c > 0) ? `表格公式 · 第 ${r} 行第 ${c} 格` : '表格公式';
        },

        /** 当前光标在 `cell` 内的**文本偏移**（不在格里 ⇒ null）。
         *
         *  ⚠️ 存**偏移**而不是 `Range` 对象：打开弹窗后焦点会移到输入框，活选区那一刻就没了；
         *    而 `Range` 一旦落进 Alpine 状态就有被套 Proxy 的风险（本仓已有
         *    「DOM 元素放进响应式状态会出 Illegal invocation」的记载，能绕开就绕开）。
         *    偏移在确定时由 `_fmlTextRange` 还原成 `Range` —— 与「替换原公式」共用同一条映射。 */
        _fmlCaretOffsets(cell) {
            try {
                const sel = window.getSelection();
                if (!sel || !sel.rangeCount) return null;
                const r = sel.getRangeAt(0);
                if (!cell.contains(r.startContainer) || !cell.contains(r.endContainer)) return null;
                const pre = document.createRange();
                pre.selectNodeContents(cell);
                pre.setEnd(r.startContainer, r.startOffset);
                const mid = document.createRange();
                mid.selectNodeContents(cell);
                mid.setEnd(r.endContainer, r.endOffset);
                return { start: pre.toString().length, end: mid.toString().length };
            } catch (e) {
                return null;      // 光标取不到不是错误（下面会回落到「格内容末尾」）
            }
        },

        /** 把 `cell` 文本里的 `[start, end)` 偏移映射成 `Range`（映射不到 ⇒ null）。
         *
         *  只走**文本节点**，与 `Element.textContent` 的口径严格一致（`<br>`/元素边界都不产生
         *  字符）—— 用 `Range.toString()` 量偏移、再用同一套走法还原，两侧同源。 */
        _fmlTextRange(cell, start, end) {
            const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT, null);
            const rng = document.createRange();
            let pos = 0, node, gotStart = false, gotEnd = false;
            while ((node = walker.nextNode())) {
                const len = node.nodeValue.length;
                if (!gotStart && start <= pos + len) {
                    rng.setStart(node, Math.max(0, start - pos));
                    gotStart = true;
                }
                if (!gotEnd && end <= pos + len) {
                    rng.setEnd(node, Math.max(0, end - pos));
                    gotEnd = true;
                }
                if (gotStart && gotEnd) return rng;
                pos += len;
            }
            return null;
        },

        /** 开弹窗。**没有选中格就拦住并给出下一步** —— 与取图「无编辑目标就拦住、不静默」
         *  同口径（用户裁定过）：不假装成功、不静默、也不打开一个没有落点的弹窗。 */
        openFormulaModal() {
            const t = this._tableEl();
            const cell = t ? t.querySelector('.rvb-cell-sel') : null;
            if (!cell) {
                this.tableError = '请先在表格里点选一个单元格（公式要写进选中的那一格）';
                return;
            }
            this.tableError = '';
            // 命中原公式 ⇒ 预填本体、并按**原定界符**定默认样式；未命中 ⇒ 空 + 行内（spec §8.5）
            const hit = window.formulaEditor.findInText(cell.textContent);
            this.fmlModalCell = cell;
            this.fmlModalTex = hit ? hit.tex : '';
            this.fmlModalDisplay = hit ? !!hit.display : false;
            this.fmlModalError = '';
            this._fmlCaretAt = this._fmlCaretOffsets(cell);
            this.fmlModalOpen = true;
            this.$nextTick(() => {
                const box = this._fmlModalInput();
                if (box) {
                    // 灌值 + 选中：用户多数是想**改**已有公式，全选省一次手工 Ctrl+A
                    box.value = this.fmlModalTex;
                    box.focus();
                    box.select();
                }
                this.fmlModalPreview();
            });
        },

        closeFormulaModal() {
            this.fmlModalOpen = false;
            this.fmlModalError = '';
            this.fmlModalCell = null;      // 不留悬空引用（表格可能已被还原/重建）
            this._fmlCaretAt = null;
            const host = this._fmlModalPreviewHost();
            if (host) host.textContent = '';   // 下次打开是新的预览，别留上一次的渲染结果
        },

        /** 行内 / 块级二选一：**改样式即重渲染预览**（`$$…$$` 与 `$…$` 的排版不同，用户要看得到）。 */
        fmlModalSetDisplay(v) {
            this.fmlModalDisplay = !!v;
            this.fmlModalPreview();
        },

        /** 实时预览：非法时 `renderPreview` 把错误文案写进预览宿主而**不抛**
         *  （`formulaEditor` 的既有契约）。⚠️ 它**不**写 `fmlModalError` —— 错误行是
         *  「点确定被拦」的专用出口，两者各司其职，别把实时渲染的抱怨混进去。 */
        fmlModalPreview() {
            window.formulaEditor.renderPreview(this._fmlModalPreviewHost(),
                                               this.fmlModalTex, this.fmlModalDisplay);
        },

        /** 确定：校验不过就**保持弹窗、只在弹窗内报错、什么都不写**；
         *  通过才落进格内，然后关弹窗。 */
        fmlModalOk() {
            const tex = this.fmlModalTex;
            const display = this.fmlModalDisplay;
            const r = window.formulaEditor.validate(tex, display);
            if (!r.ok) {
                this.fmlModalError = r.error;
                return;                       // ⚠️ 不关弹窗：用户填的内容不能被丢弃
            }
            const err = this._fmlWriteIntoCell(tex, display);
            if (err) { this.fmlModalError = err; return; }
            this.closeFormulaModal();
        },

        /** 把公式写进选中格。返回错误文案（无错返回空串）。
         *
         *  落点两档（spec §8.5）：
         *    · 格内**已有**公式 ⇒ **原地替换** `[start, end)`（区间含定界符，`findInText` 给的）；
         *    · 没有 ⇒ 落到**打开弹窗时的光标处**（取不到光标才回落到格内容末尾，绝不静默丢弃）。
         *  ⚠️ 写入走 `document.execCommand('insertText')` —— 与行编辑器 `exec` **同一条既有
         *    通道**（contenteditable 的插入/替换都靠它，且自带浏览器撤销栈）。
         *  ⚠️ 定界符按当前选择，不按「原来是什么」：用户手里的「行内/块级」开关就是最终裁决
         *    （默认值已按原公式给好 ⇒ 不改开关时**保留原定界符类型**）。
         *  ⚠️ **不序列化、不写 `edits`**：表格仍由用户点「保存」走 `saveEdit` 的表格分支落库。 */
        _fmlWriteIntoCell(tex, display) {
            const cell = this.fmlModalCell;
            const t = this._tableEl();
            if (!cell || !t || !t.contains(cell)) {
                return '目标单元格已不在表格里了（表格被还原或重建过），请关掉弹窗重新点选';
            }
            const body = display ? '$$' + tex + '$$' : '$' + tex + '$';
            const hit = window.formulaEditor.findInText(cell.textContent);
            const at = hit || this._fmlCaretAt;
            let rng = at ? this._fmlTextRange(cell, at.start, at.end) : null;
            if (!rng) {
                rng = document.createRange();
                rng.selectNodeContents(cell);
                rng.collapse(false);          // 回落到格内容末尾（比"什么都不发生"诚实）
            }
            cell.focus();
            const sel = window.getSelection();
            if (!sel) return '浏览器没有可用的选区，无法写入';
            sel.removeAllRanges();
            sel.addRange(rng);
            document.execCommand('insertText', false, body);
            return '';
        },

        /** 图片块：删除图片（**只解除引用，不删文件**）。
         *
         *  只动编辑器宿主，真正的落库发生在「保存」时 ⇒ 用户点「取消」即可撤回
         *  （spec §8.4：删除必须保留会话内撤回能力）。文件的物理清理不在这里 ——
         *  它归导入成功之后的存储治理（spec §19），那时会话已结束、撤回需求自然到期。 */
        imgDelete() {
            const host = this._imgHost();
            if (!host) return;
            if (!host.querySelector('img')) { this.imgError = '当前没有可删除的图片'; return; }
            if (!host.querySelector('img')) { this.imgError = '当前没有可删除的图片'; return; }
            this.imgError = '';
            // 连**壳**一起去掉（对齐包裹的 span/div）：只摘 `<img>` 会剩一堆空标签，
            // 渲染出来仍是一个空行。
            host.innerHTML = '';
        },

        // ── 图片替换的三个入口（spec §8.4）────────────────────────────
        //
        // 三条路**汇到同一落点**：`_uploadImage` → `POST /import/review/{id}/image`
        // → 换成新文件名 → 宿主里的 `img.src` 换掉（绝对路径，编辑器里立刻可见）。
        // 保存时才把绝对化还原成相对 `imgs/…` 写进 `edits`。

        /** 三入口共用：Blob → 落盘 → 换宿主里的 `img.src`。
         *
         *  失败时**只置 `imgError`，不动块状态**（原图仍在、`edits` 未写）——
         *  半途把用户的图弄没了比失败更坏。 */
        async _imgApplyBlob(blob) {
            if (!blob) return;
            this.imgError = '';
            try {
                const filename = await this._uploadImage(blob);
                this._putImgInHost(this._imgHost(), this.imgAbsBase() + filename);
            } catch (e) {
                this.imgError = String((e && e.message) || e);
            }
        },

        /** 把一个 img 放进图片块编辑宿主（有则换 src，无则补一个）。
         *
         *  补的时候若已有**对齐包裹**（`<span>` / `<div>`），要塞进包裹内部 ——
         *  直接 append 到宿主会把包裹挤到一边，保存时对齐就丢了。 */
        _putImgInHost(host, url) {
            if (!host) return false;
            const img = host.querySelector('img');
            if (img) { img.setAttribute('src', url); return true; }
            const shell = host.firstElementChild;
            const neu = document.createElement('img');
            neu.setAttribute('src', url);
            neu.setAttribute('alt', 'Image');
            if (shell && shell.tagName !== 'IMG') shell.appendChild(neu);
            else host.appendChild(neu);
            return true;
        },

        /** 入口一：点击选文件（按钮 → 隐藏的 `<input type=file>`） */
        imgChooseFile(e) {
            const wrap = e && e.target && e.target.closest('.rvb-img-wrap');
            const input = wrap && wrap.querySelector('input[type="file"]');
            if (input) input.click();
        },

        imgReplaceFromFile(e) {
            const f = e.target && e.target.files && e.target.files[0];
            if (f) this._imgApplyBlob(f);
            if (e.target) e.target.value = '';   // 复位：允许连续选**同一个**文件
        },

        /** 入口二：拖拽文件到块上 */
        imgReplaceFromDrop(e) {
            const dt = e.dataTransfer;
            const f = dt && dt.files && dt.files[0];
            if (f) this._imgApplyBlob(f);
        },

        /** 图片块的对齐（spec §8.6，左 / 中 / 右）。
         *
         *  用 `mdInline.wrap` 的**同行 span** 形式：
         *  `<span style="display:block;text-align:X">…</span>` —— **单行、不新增行**，
         *  故对 `parse_markdown` 的切分零风险（spec §8.6 实测③：独占行的 div 会多切出一条伪条文）。
         *  语料里的图片行本就是单行包裹形态（`<div style="text-align: center;">…</div>`），
         *  这里统一改写成本仓的 span 形式。
         *
         *  ⚠️ 取 `img.outerHTML` 而不是把整串重新包一层：容器里原本的对齐包裹（div 或旧 span）
         *  会被**替换掉**；若用整串包，就会套成 span>div 两层，而内层 div 的居中仍然生效 ——
         *  「改成左对齐」看着毫无变化。 */
        imgAlign(v) {
            const host = this._imgHost();
            if (!host) return;
            const img = host.querySelector('img');
            if (!img) {
                this.imgError = '此地没有图片可对齐（先用「选文件」或取图放一张）';
                return;
            }
            this.imgError = '';
            host.innerHTML = window.mdInline.wrap(img.outerHTML, v || null);
        },

        /** 入口三：粘贴（截图 / 复制的位图） */
        imgReplaceFromPaste(e) {
            const items = (e.clipboardData && e.clipboardData.items) || [];
            for (const it of items) {
                if (it.type && it.type.indexOf('image/') === 0) {
                    this._imgApplyBlob(it.getAsFile());
                    return;
                }
            }
        },

        // ── 裁剪（spec §8.4：前端 canvas 裁切 → **另存为新文件**）──────────

        /** 进入裁剪模式。图没加载完就**不进** —— 那时 `naturalWidth` 是 0，
         *  换算不出自然像素，拖出来的框会裁到错的地方。 */
        imgStartCrop() {
            const host = this._imgHost();
            const img = host && host.querySelector('img');
            if (!img) { this.imgError = '当前没有可裁剪的图片（先用「选文件」或取图放一张）'; return; }
            if (!img.complete || !img.naturalWidth) { this.imgError = '图片还没加载完，稍后再试'; return; }
            this.imgError = '';
            this.cropMode = true;
            this.cropRect = null;
            this._cropImg = img;
        },

        imgCancelCrop() {
            this.cropMode = false;
            this.cropRect = null;
            this._cropDrag = null;
            this._cropImg = null;
        },

        /** 裁剪框的定位。`cropRect` 以**图**为原点，而覆盖层挂在 **stage** 上
         *  ⇒ 必须加上「图在 stage 内的偏移」。
         *
         *  ⚠️ 不能假定图贴着 stage 左上角：语料里的图片行本就带
         *  `<div style="text-align: center;">` ⇒ 图是**水平居中**的。
         *  实测：251px 宽的图在 598px 宽的 stage 里左偏 173px —— 直接按 rect 定位，
         *  覆盖层整体右移一大截（拖左半边，框却画在右半边）。 */
        cropSelStyle() {
            const r = this.cropRect;
            const im = this._cropImg;
            if (!r || !im) return 'display:none';
            const stage = im.closest('.rvb-img-stage');
            let ox = 0, oy = 0;
            if (stage) {
                const sb = stage.getBoundingClientRect();
                const ib = im.getBoundingClientRect();
                ox = ib.left - sb.left;
                oy = ib.top - sb.top;
            }
            return `left:${ox + r.x}px;top:${oy + r.y}px;width:${r.w}px;height:${r.h}px`;
        },

        /** 裁剪框的手势与选框**共用** `imagePicker` 的纯几何（normalize / hitHandle / resize /
         *  clampRect）—— 同一套交互在两处出现，没有理由写两份实现。 */
        onCropDown(e) {
            if (!this.cropMode || e.button !== 0) return;
            const img = this._cropImg;
            if (!img) return;
            e.preventDefault();
            const box = img.getBoundingClientRect();
            const px = e.clientX - box.left, py = e.clientY - box.top;
            const ip = window.imagePicker;
            const handle = this.cropRect ? ip.hitHandle(this.cropRect, px, py, ip.HANDLE_TOL) : null;
            if (handle === 'move') {
                this._cropDrag = { mode: 'move', x0: px, y0: py,
                                   orig: { x: this.cropRect.x, y: this.cropRect.y,
                                           w: this.cropRect.w, h: this.cropRect.h } };
            } else if (handle) {
                this._cropDrag = { mode: 'resize', handle: handle };
            } else {
                this.cropRect = ip.normalize(px, py, px, py);
                this._cropDrag = { mode: 'new', x0: px, y0: py };
            }
            const move = (ev) => this._cropMove(ev, box);
            const up = () => {
                this._cropDrag = null;
                window.removeEventListener('mousemove', move);
                window.removeEventListener('mouseup', up);
                if (this.cropRect) {
                    this.cropRect = window.imagePicker.clampRect(this.cropRect, box.width, box.height);
                }
            };
            window.addEventListener('mousemove', move);
            window.addEventListener('mouseup', up);
        },

        _cropMove(ev, box) {
            const d = this._cropDrag;
            if (!d) return;
            const ip = window.imagePicker;
            const px = ev.clientX - box.left, py = ev.clientY - box.top;
            if (d.mode === 'new') {
                this.cropRect = ip.normalize(d.x0, d.y0, px, py);
            } else if (d.mode === 'resize') {
                this.cropRect = ip.resize(this.cropRect, d.handle, px, py);
            } else {
                // 整体平移：夹的是**位移**而不是矩形（夹矩形会在贴边时把框压小）
                const x = Math.max(0, Math.min(d.orig.x + (px - d.x0), box.width - d.orig.w));
                const y = Math.max(0, Math.min(d.orig.y + (py - d.y0), box.height - d.orig.h));
                this.cropRect = { x: x, y: y, w: d.orig.w, h: d.orig.h };
            }
            this.cropRect = ip.clampRect(this.cropRect, box.width, box.height);
        },

        /** 确认裁剪：显示坐标 → 自然像素 → 画到新画布 → **走共用落盘端点** → 换 src。
         *  原图不动（新文件名保证），所以「取消」就能撤回。 */
        async imgDoCrop() {
            const img = this._cropImg;
            if (!img || !this.cropRect) { this.imgError = '请先在图上拖出要保留的区域'; return; }
            const box = img.getBoundingClientRect();
            const nat = window.imageCrop.toNatural(this.cropRect, box.width, box.height,
                                                   img.naturalWidth, img.naturalHeight);
            if (!nat) { this.imgError = '裁剪区域太小（或图还没加载完）'; return; }
            this.imgError = '';
            try {
                const canvas = window.imageCrop.drawCrop(img, nat);
                const blob = await new Promise((res, rej) => {
                    canvas.toBlob((b) => b ? res(b) : rej(new Error('画布导出失败')), 'image/png');
                });
                const filename = await this._uploadImage(blob);
                this._putImgInHost(this._imgHost(), this.imgAbsBase() + filename);
                this.imgCancelCrop();
            } catch (e) {
                this.imgError = '裁剪失败：' + String((e && e.message) || e);
            }
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
            if (!b) return;
            // 图片块走**另一条**准入判据（见 `canEditImage`）：它不在文本白名单里，
            // 但只要有 span 就能安全回写。两条判据都要求 `hasSpan`。
            const isImg = this.imageLabels.indexOf(b.label) >= 0;
            if (isImg ? !this.canEditImage(b) : !this.canEdit(b)) return;
            // ⚠️ 表格块**解析不出 `<table>` 就不进编辑态**（缺 `md_html`、或 `md_html` 里根本没有表），
            //    且守卫必须在 `editingIdx` 赋值**之前** —— 放在下面 `$nextTick` 里会变成
            //    "已进编辑态、编辑器却是空的"（用户看到空白框，且行编辑器工具条会一并露出来）。
            //    也绝不能回落到 `b.content`：那是 OCR 的**去样式版**，写回会丢 `border=1` 与
            //    `margin: auto`（边框与居中的唯一来源，渲染侧无补偿 CSS）。宁可不让改。
            //    两种失效原因（缺 md_html / 里面没表）**一并**由 `parse` 的 null 覆盖，保持
            //    「要么完整进编辑态、要么完全不进」这一条不变量。`parse` 是纯 DOM 构造、
            //    不依赖宿主元素存在，故可安全前移到 `$nextTick` 之外。
            // ⚠️ **取「当前内容」而不是导入时的 `b.md_html`**（Task 6 复审的 Important 修复）：
            //    用 `b.md_html` 时，「改表 → 保存 → 再点纠正」会从**原始表**重建编辑器 ⇒
            //    此时再保存就把 `edits[i]` 覆盖回原始表 ⇒ **第一次的编辑静默丢失**（两步用户动作可达）。
            // ⚠️ **绝不能写成 `this.contentOf(b)`**：表格块的 `contentOf` 无编辑时回落 `b.content` ——
            //    那是 OCR 的**去样式版**，会把 `border=1` / `margin: auto`（边框与居中的唯一来源、
            //    渲染侧无补偿 CSS）悄悄丢掉。Task 3 当初选 `md_html` 正是为此。这里只在
            //    **确实存在编辑**时才用 `edits[i]`（那是一条单表字符串）。
            //    ⚠️ serialize 契约不受影响：`edits[i]` 是单张表 ⇒ `parse` 缓存 `_rvbSource` ⇒
            //    未改动的重进编辑仍**逐字节**往返。
            const src = this.edits[b.__i] ?? b.md_html;
            const t = (b.label === 'table') ? window.tableEditor.parse(src) : null;
            if (b.label === 'table' && !t) return;
            if (t) this._absTableImgs(t);   // ⚠️ 必须在 `_markTableEditable` **之前**（见该函数注释）
            this.tableError = '';          // 清掉上一次的表格报错（否则会跟着下一块显示）
            this.imgError = '';
            this.formulaError = '';        // 同上：清掉上一条公式块的残留错误/降级说明
            this.formulaNote = '';
            this.editingIdx = idx;
            this.$nextTick(() => {
                const box = this._editBox();
                if (box) box.innerHTML = '';
                if (isImg) { this._buildImgEditor(b); return; }
                if (b.label === 'display_formula') { this._buildFormulaEditor(b); return; }
                if (b.label === 'table') {
                    this._markTableEditable(t);
                    this._tableSnapshots = [t.outerHTML];       // 快照栈的起点（Task 5 起用）
                    this._selAnchor = null;                     // 换表必须重置锚点（旧锚点属别的表 / 已分离）
                    const host = this._tableHost();
                    if (host) { host.innerHTML = ''; host.appendChild(t); }
                    this._selectTable();                        // 默认选中首格（工具条操作都作用于选区）
                    // ⚠️ 进编辑后必须把**块顶**对准容器顶（用户实测「点纠正后不是到表格首行」）：
                    //    进编辑会把块体（`.rvb-block-bd`/`.rvb-block-meta`）藏起来 ⇒ 块**变矮** ⇒
                    //    浏览器的 **scroll anchoring** 补偿式地改 `scrollTop`，把块顶推出视野
                    //    （实测：块顶 +260 → −36，正好把工具条顶掉）。
                    //    补两次：一次立刻、一次在下一帧 —— 只补一次会被那笔补偿再推走。
                    this._scrollRightTo(idx, 'start');
                    requestAnimationFrame(() => this._scrollRightTo(idx, 'start'));
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

        /** 单元格上按下左键：记录「可能开始拖拽框选」的起点。
         *
         * ⚠️ **不 `preventDefault`** —— 点一下必须仍能把光标放进格子里打字。
         *    只有真的拖到**另一个格**时才升级成框选（见 `onTableCellOver`）。
         * ⚠️ 顺带清掉上一轮拖拽留下的 `_suppressClick`：拖拽若在格外松开，那次 `click`
         *    根本不会来，抑制位会一直挂着把**下一次普通单击**吞掉。 */
        onTableCellDown(ev) {
            this._suppressClick = false;
            this._dragged = false;
            if (ev.button !== 0) { this._dragFrom = null; return; }
            const t = this._tableEl();
            const cell = ev.target.closest('td,th');
            this._dragFrom = (cell && t && t.contains(cell)) ? cell : null;
        },

        /** 拖拽经过另一个格：以起点为**锚点**实时扩成矩形（用户实测要的就是这个手势）。
         *
         * ⚠️ 拖拽时必须 `preventDefault` —— 否则浏览器同时做**原生文字选区**（一片蓝），
         *    与我们自己的 `.rvb-cell-sel` 打架。 */
        onTableCellOver(ev) {
            if (!this._dragFrom) return;
            if (!ev.buttons) { this._dragFrom = null; return; }   // 在格外松开 ⇒ 放弃本次拖拽
            const t = this._tableEl();
            const cell = ev.target.closest('td,th');
            if (!t || !cell || !t.contains(cell) || cell === this._dragFrom) return;
            ev.preventDefault();
            if (!this._dragged) { this._selAnchor = this._dragFrom; this._dragged = true; }
            this._selectTable(cell, true);
        },

        /** 松开左键：结束拖拽。
         *
         * ⚠️ `_suppressClick` 只对**一种情形**有用，**不要**因为探针删它就当死代码删掉：
         *    跨格拖拽（起点格 ≠ 松手格）时，浏览器把 `click` 派发到两格的**共同祖先**
         *    （`<table>`）⇒ `closest('td,th')` 得 null、处理器直接 return，本来就不会塌。
         *    **实测**：把这行删掉，跨格拖拽那条探针用例**照样全绿**（所以那条用例并不覆盖这里）。
         *    真正会塌的是「拖出去**再拖回起点格**才松手」—— 那时 `click` 落在起点格本身，
         *    走「起选」分支把矩形塌成一格。这条路径**目前没有探针覆盖**（已知留白，记在 ledger）。 */
        onTableCellUp() {
            if (!this._dragFrom) return;
            this._dragFrom = null;
            if (this._dragged) this._suppressClick = true;
        },

        /** 表格单元格的点击 / Shift+点：由容器**事件委托**处理（格是动态生成的，逐个绑会随重建失效） */
        onTableCellClick(ev) {
            if (this._suppressClick) { this._suppressClick = false; return; }   // 刚拖拽完，见 onTableCellUp
            const cell = ev.target.closest('td,th');
            if (!cell) return;
            const t = this._tableEl();
            if (!t || !t.contains(cell)) return;
            this._selectTable(cell, ev.shiftKey);
        },

        /** 表格编辑态的选择：点 = 单格；Shift+点 = 以**锚点**到当前格为对角扩成矩形区域（spec §2.3）。
         *
         * ⚠️ **选中态存在元素 class 上**（`rvb-cell-sel`）、**不进 Alpine 状态** —— 本仓既定口径：
         *    Alpine 状态里只放下标/布尔，DOM 元素会被 Proxy 包壳出问题（见 `_makeLine` 注释）。
         *    `_selAnchor` 只是「Shift 扩选」的锚点引用，由调用方在换表/还原时重置。
         *
         * ⚠️ **锚点只在「起选」时移动**（`extend` 为假），扩选时**不动** —— 原写法在 `_selectTable`
         *    末尾无条件 `_selAnchor = focus`，配合「Shift 时只跳过赋值」的调用方，会让锚点**逐段漂移**
         *    到上一次点击处：连按两次 Shift+点选出的不是 [首次点击 … 当前]，而是 [上次点击 … 当前]
         *    （spec §2.3 要的是前者）。**注意 `focus`（当前格）与 `anchor`（起点）不可混同** ——
         *    最终写回的必须是 `anchor`，写 `focus` 就是这次修正前的漂移写法。 */
        _selectTable(anchorCell, extend) {
            const t = this._tableEl();
            if (!t) return;
            const focus = anchorCell || t.querySelector('.rvb-cell-sel') || t.querySelector('td,th');
            const anchor = (extend && this._selAnchor) || focus;
            window.tableEditor.selectRange(t, anchor, focus);
            this._selAnchor = anchor;
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
            if (!el) return;      // `_focusLine()` 在空编辑区可能返回 null（防御第二道）
            const line = this._makeLine('');
            el.parentNode.insertBefore(line, el.nextSibling);
            line.focus();
        },

        _deleteLine(el) {
            if (!el) return;      // 同上：没有聚焦行时不动作，绝不抛 `null.parentNode`
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
            const b = this.blocks[this.editingIdx];
            if (b && this.imageLabels.indexOf(b.label) >= 0) {
                const host = this._imgHost();
                if (!host) return;
                // ★ **没动过就不写 `edits`**：写了会凭空出现「已修改」，且 `_cropUrl` 的
                //   edited-guard 会让只读态本该显示的 bbox 裁图**消失** ——
                //   用户什么都没做，图却变了。
                if (host.innerHTML !== this._imgBaseline) {
                    this.edits[b.__i] = this._relImgs(host.innerHTML);
                }
                // 与表格分支同口径：退出编辑态必须**释放编辑 DOM**（`x-show` 只是隐藏）
                host.innerHTML = '';
                this.editingIdx = -1;
                this.schedulePreview();
                return;
            }
            if (b && b.label === 'table') {
                const t = this._tableEl();
                if (!t) return;
                const v = window.tableEditor.validate(t);
                if (!v.ok) { this.tableError = '表格结构不合法：' + v.reason; return; }
                this.tableError = '';      // 成功路径也清（与失败分支对称，不留陈旧状态）
                // 相对化：编辑态里的 src 是绝对的（见 `_absTableImgs`），写进 md 必须还原成
                // `imgs/…`。对「未改动」返回的 `_rvbSource` 是幂等的（它本就是相对串）。
                this.edits[b.__i] = this._relImgs(window.tableEditor.serialize(t));
                // ⚠️ **退出编辑态必须释放表格编辑 DOM**：`.rvb-table-edit` 只靠 `x-show` 隐藏，
                //    不释放的话那张 `contenteditable` 表会**留在 DOM 里**（隐藏但存在）⇒
                //    「是否还在编辑态」在 DOM 上不可分辨（f14_16 的 exit 判据正是数它）。
                //    也必须**在** `editingIdx = -1` **之前**清（`_tableHost()` 是按 editingIdx 找的）。
                const host = this._tableHost();
                if (host) host.innerHTML = '';
                this.editingIdx = -1;
                this.schedulePreview();
                return;
            }
            if (b && b.label === 'display_formula') {
                const box = this._fmlSrc();
                if (!box) return;
                const tex = box.value;
                const display = this._fmlDisplay;
                // ★ **未改动且对齐没被动过 ⇒ 一律不写 `edits`**（与图片块同口径）。
                //   写了会凭空出现「已修改」，且 compose 会把该行重写一遍 —— 任何序列化差异
                //   都会**静默改写**用户没动过的公式行（真实夹具的 ` $$ … $$ ` 首尾空格极脆）。
                //   ⚠️ 比的是 `_fmlAlignBaseline`（进编辑态时**恢复出来的**对齐），不是 null：
                //      重进一个**已对齐**的公式时 `_fmlAlign` 初值就是 'center'，拿 null 比会
                //      误判成「改过」⇒ 未改动也写一条 edits。
                if (tex === this._fmlBaseline && this._fmlAlign === this._fmlAlignBaseline) {
                    this.formulaError = '';
                    this.editingIdx = -1;
                    this.schedulePreview();
                    return;
                }
                // 校验失败 ⇒ 保持编辑态、只在块内报错、不写 edits
                // （沿用表格编辑态「校验失败保留编辑态、块内报错」的既有形状）
                const r = window.formulaEditor.validate(tex, display);
                if (!r.ok) { this.formulaError = r.error; return; }
                this.formulaError = '';
                // 先组回整行（`buildLine` 保留原行首尾空白），再按对齐做**同行 span** 包裹
                // （`mdInline.wrap` 单行、不新增行 ⇒ 对 `parse_markdown` 的切分零风险）。
                const line = window.formulaEditor.buildLine(tex, display, this._fmlSrcLine);
                this.edits[b.__i] = window.mdInline.wrap(line, this._fmlAlign);
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
            this.schedulePreview();          // 保存后刷新切分预览（防抖）
        },

        cancelEdit() {
            // 与 `saveEdit` 的表格分支同口径：退出编辑态释放表格编辑 DOM（`.rvb-table-edit` 是
            // `x-show` 隐藏、节点仍在 ⇒ 不释放则「否在编辑态」在 DOM 上不可分辨）。行编辑块该容器本就是空的。
            const host = this._tableHost();
            if (host) host.innerHTML = '';
            const imgHost = this._imgHost();
            if (imgHost) imgHost.innerHTML = '';
            this.tableError = '';
            this.imgError = '';
            this.formulaError = '';
            this.formulaNote = '';
            this.editingIdx = -1;
            this.schedulePreview();
        },

        // ── P2：切分预览（spec §11.4）「这次编辑改了切分吗」 ──

        /** 首次调用就是**基线**（edits 为空 ⇒ 与导入前的切分一致）。 */
        async refreshPreview() {
            // 编辑态或提交中都不刷新：前者的中间态没有意义，后者的响应会在结果区被覆盖后
            // 又写回预览（Task 8 已判 `editingIdx`，Task 9 把 `submitting` 补上）。
            if (this.editingIdx >= 0 || this.submitting) return;
            this.previewBusy = true;
            this.previewErr = '';
            try {
                const resp = await fetch(`/import/review/${this.taskId}/preview`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({edits: this.edits}),
                });
                if (!resp.ok) throw new Error((await this._json(resp)).detail || resp.status);
                const data = await this._json(resp);
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

        /** 变更后 400 ms 防抖（实测最重产物 109 ms ⇒ 无需 spec §11.4 的降级方案）
         *
         *  ★ 这里**同时**重扫搜索高亮：块的正文是 `x-html="renderBlock(b)"`，**任何重渲染都会
         *    销毁块内的 `<mark>`** —— 保存编辑（以及 Task 10 的替换）之后必须重新施加。
         *    挂在**这一个**收尾点上而不是逐个调用点，是因为调它的正是「块内容已变」的几条路径
         *    （`saveEdit` 的五个分支 + `cancelEdit`），漏一处就会悄悄少一块高亮。
         *    ⚠️ 空闲时（没搜索词、上一轮也没标过）`findMatches` 内部**不建 DOM 查询**，接近零开销。
         *    ⚠️ 用的是**另一个**防抖句柄（`_searchTimer`），不要与 `_previewTimer` 合并。 */
        schedulePreview() {
            clearTimeout(this._previewTimer);
            this._previewTimer = setTimeout(() => this.refreshPreview(), 400);
            this.scheduleSearch();
        },

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

        // ── P4 Task 9：跨块搜索（工具条 + 计数 + DOM 高亮；替换/跳转是 Task 10）──────
        //
        // 口径（用户裁定 D2）：
        //  · **搜索范围 = 所有「进 md 的块」**（label ∉ 后端下发的 `not_in_markdown`），
        //    与「有没有 span」**无关** —— span 只决定左栏能否定位，不决定文本进不进 md。
        //    顺手加 `hasSpan` 条件会造成「看得见却搜不到」，是**错误**的收窄。
        //  · **计数域 = 字符串域**（`countIn(contentOf(b), q)`）；而高亮域是**文本节点**域
        //    （不跨元素边界）。命中跨 `<strong>` / 跨表格单元格时两个域**必然不等** ——
        //    这是**刻意**取舍：做精确对位要维护「源码偏移 ↔ DOM 文本节点」映射表，收益不抵
        //    复杂度（旧源码审查页注释里记过同一结论）。
        //    ⇒ 显示的数**一律用字符串域**；**绝不**为「两数相等」去改高亮实现。
        //  · 本页**没有 textarea** ⇒ 与旧源码审查页（绑在源码偏移上）是**两套模型**，不共用。

        /** 该块是否进 markdown（D2 的**唯一**计数判据；与有无 span 无关）。 */
        inMarkdown(b) {
            return this.notInMarkdown.indexOf(b.label) < 0;
        },

        /** 输入即全量扫描（真实产物 4000 块）⇒ 必须防抖；照抄本文件 `schedulePreview`。 */
        scheduleSearch() {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => this.findMatches(), 400);
        },

        /** 「清空」：**立即**撤（不等防抖），并把搜索词一并清掉（输入框由 `x-model` 跟随）。 */
        clearSearch() {
            this.searchQuery = '';
            clearTimeout(this._searchTimer);
            this.findMatches();
        },

        /** 扫描所有「进 md 的块」：`N` = 命中总数（字符串域），`M` = 落在**可编辑块**上的命中数。
         *
         *  ⚠️ **不得**把 `searchQuery` / `searchHits` 读进 `x-for` 里的块绑定：那会让每次输入
         *     在 4000 块上各重算一遍（本仓记过 2.8 万遍 / 进编辑 857ms 的事故）。
         *     块内的高亮一律走 `markInto` **命令式**改 DOM。 */
        findMatches() {
            const q = this.searchQuery;
            const prev = this.searchHits;
            let total = 0, repl = 0;
            const hits = [];
            if (q) {
                for (let i = 0; i < this.blocks.length; i++) {
                    const b = this.blocks[i];
                    if (!this.inMarkdown(b)) continue;     // ← 不进 md 的块不计数（D2）
                    const n = window.textSearch.countIn(this.contentOf(b), q);
                    if (!n) continue;
                    total += n;
                    if (this.canEdit(b)) repl += n;        // 可替换 = 落在可编辑块上的命中
                    hits.push({ blockIdx: i, __i: b.__i, count: n });
                }
            }
            this.searchHits = hits;
            this.searchIdx = hits.length ? 0 : -1;
            this.searchInfo = q ? `${total} 个匹配 · ${repl} 个可替换` : '';
            // 只在实际有「落在不可编辑块上的命中」时才说明原因（D2 的诚实口径）
            this.searchMsg = (q && repl < total) ? `其中 ${total - repl} 处所在块不可编辑` : '';
            // 空词且上一轮也没标过 ⇒ 无事可做（别白建一次 4000 项的 DOM 查询）
            if (q || (prev && prev.length)) this._syncMarks(prev, hits, q);
        },

        /** 把 DOM 上的 `<mark>` 与本次命中对齐（**命令式**，只碰已渲染的块正文宿主）。
         *
         *  ★ 换词后旧标记的清理口径：**只清「上一轮命中、本轮不再命中」的块**。
         *  为什么不是「清全部」、也不是「清上一轮全部」：
         *   · 本轮**仍命中**的块交给 `markInto` 自带的**前置 `clearMarks`**（幂等）——
         *     重复搜同一词因此不会把 `<mark>` 再包一层；
         *   · 上一轮命中、本轮不再命中的块若不清，换词（含清空）后旧 `<mark>` 会**留在页面上**；
         *   · 两处**都**清也不会错，但会让「谁在保证幂等」变得不可分辨 —— 任一处失效都测不出来
         *     （本仓「变异不完整会被误读成探针失明」的教训）。
         *  ⚠️ 只对**命中块**插标记（不遍历所有块）—— 4000 块全扫是白费。 */
        _syncMarks(prev, hits, q) {
            const bds = this._blockBds();
            const now = new Set(hits.map((h) => h.blockIdx));
            for (const h of prev || []) {
                if (now.has(h.blockIdx)) continue;         // 仍命中的交给 markInto 去重
                const host = bds[h.blockIdx];
                if (host) window.textSearch.clearMarks(host);
            }
            if (!q) return;
            for (const h of hits) {
                const host = bds[h.blockIdx];
                if (host) window.textSearch.markInto(host, q);   // 自带 clearMarks ⇒ 幂等
            }
        },

        /** 块正文宿主表 `{块下标: .rvb-block-bd}`（一次取齐）。
         *
         *  ⚠️ 按 `data-block-idx` 建表，**不**依赖 `querySelectorAll` 的返回顺序：顺序一致只是
         *     模板的**隐含**约定（`x-for` 的渲染顺序），而 `data-block-idx` 是**显式**契约
         *     （模板与组件里到处按它取元素）—— 哪天列表加了过滤或虚拟化，前者会**静默错位**
         *     （把标记插到别的块上）。
         *  ⚠️ 一次取齐而不是逐块 `querySelector`：命中几百块时后者是 O(n²) 级的 DOM 查询。 */
        _blockBds() {
            const map = {};
            const list = this.$refs.list;
            for (const el of (list ? list.querySelectorAll('.rvb-block') : [])) {
                const i = Number(el.getAttribute('data-block-idx'));
                const bd = el.querySelector('.rvb-block-bd');
                if (Number.isFinite(i) && bd) map[i] = bd;
            }
            return map;
        },

        // ── P2：提交链路（compose → confirm → 轮询） ──

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
                if (!cr.ok) throw new Error((await this._json(cr)).detail || ('compose ' + cr.status));
                const md = (await this._json(cr)).md_text;
                // md 走 raw body（text/plain）：表单字段有 1MB 上限，大规范会被 400 拒掉
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

        /** 给表里**所有**格挂上可编辑态（`enterEdit` / `_restoreTable` / `tableOp` 成功分支共用）。
         *
         * ⚠️ **每一个「会造新格」的操作之后都必须再调一次**：`insertRow` / `insertCol` / `splitCell`
         *    都用 `document.createElement('td')` 造格，新格**不会**自动带 `contenteditable`
         *    ⇒ 用户实测到的「已合并的格拆分后，只有原来带内容的格能编辑，新格打不了字」。
         *
         * ⚠️ **不要**图省事改成「把属性挂到 `<table>` 上、靠继承覆盖新格」—— 那看着更干净、
         *    也确实能让新格可编辑，但**实测 `Ctrl+A` 的选区会从「格内文字」(2 字符)
         *    变成「整张表」(258 字符)** ⇒ 用户在格内全选再打字会**把整张表替换掉**。
         *    （本轮先按那个写法改，被 `f14_6` 当场抓住，才有上面这段实测数据。）
         */
        _markTableEditable(t) {
            t.querySelectorAll('td,th').forEach((c) => c.setAttribute('contenteditable', 'true'));
        },

        /** 表格结构操作的统一入口：**压快照 → 执行 → 复验**。
         *
         * ⚠️ 快照必须在**操作前**压：这是「撤销」按钮唯一的数据来源（spec §2.4）。
         * ⚠️ 每次结构操作后立刻 `validate`：非法就**整体回滚到刚压的快照**，
         *    而不是把用户留在半坏的表里（校验器只会在保存时再挡一次，那时已经晚了）。
         * ⚠️ 选中格回落：Task 5 起优先取**选中格**（`.rvb-cell-sel`）；`|| t.querySelector('td,th')`
         *    是兜底 —— 表结构非法时 `selectRange` 会拒绝对选区打标（`g.err`），此时仍有格可用。
         */
        tableOp(name) {
            const t = this._tableEl();
            const cell = (t && t.querySelector('.rvb-cell-sel')) || (t && t.querySelector('td,th'));
            if (!t || !cell) return;
            const snap = t.outerHTML;
            let ok = false;
            // ⚠️ **必须 try/catch**：结构操作是「就地改 DOM」，途中可能**抛异常**（不是返回 false）。
            //    已复现的一例：rowspan 表上调 `insertColAfter` —— 第 i 行取到的 `ref` 可能是
            //    上一行 rowspan 格的 DOM（不属于本 `<tr>`）⇒ `row.insertBefore` 抛 `NotFoundError`，
            //    此时**前面的行已经插了格**（锯齿表）、而异常发生在任何 `validate`/`_restoreTable`
            //    **之前** ⇒ 不回滚、`tableError` 不置、用户毫无提示。包 catch 后任何抛出都归一到
            //    下面的失败分支：整体回滚 + 块内报错，绝不把用户留在半坏的表里。
            try {
                switch (name) {
                    case 'insertRowBefore': ok = window.tableEditor.insertRow(t, cell, true); break;
                    case 'insertRowAfter': ok = window.tableEditor.insertRow(t, cell, false); break;
                    case 'insertColBefore': ok = window.tableEditor.insertCol(t, cell, true); break;
                    case 'insertColAfter': ok = window.tableEditor.insertCol(t, cell, false); break;
                    case 'deleteRow': ok = window.tableEditor.deleteRows(t, [cell]); break;
                    case 'deleteCol': ok = window.tableEditor.deleteCols(t, [cell]); break;
                    case 'merge': ok = window.tableEditor.mergeCells(
                        t, [...t.querySelectorAll('.rvb-cell-sel')]); break;
                    case 'split': ok = window.tableEditor.splitCell(t, cell); break;
                }
            } catch (e) {
                ok = false;      // 任何抛出都走下面的回滚分支（绝不把用户留在半坏的表里）
                // ⚠️ **必须留痕**：没有这行时，一切抛出（含 `window.tableEditor` 未加载这类
                //    **接线错误**）都被下面统一转述成「该操作在当前选区不可用」、控制台零痕迹，
                //    排查时无从下手。日志只做可观测性，不改失败语义。
                console.warn(e);
            }
            const v = window.tableEditor.validate(t);
            if (!ok || !v.ok) {
                this._restoreTable(snap);
                this.tableError = ok ? ('表格结构不合法：' + v.reason) : '该操作在当前选区不可用';
                return;
            }
            this.tableError = '';
            // ✅ 结构操作**成功后**必须处理「锚点格被整个删掉」的情形：
            //    `merge`/`deleteRows`/`deleteCols` 都可能把锚点格从 DOM 里摘掉 ⇒ `_selAnchor`
            //    指向**已分离**的节点 ⇒ 下一次 Shift+点走 `selectRange`，`rows.indexOf(分离 tr)` = -1
            //    ⇒ `g.grid[-1].indexOf` 抛 **TypeError**（抛出点在 `@click` 里，**不在**本函数的
            //    try/catch 内 ⇒ 用户视角是「Shift+点毫无反应」+ 控制台报错）。
            //    这里只把**失效的**锚点置空（不重置选区）：置空后下一次 `_selectTable` 会以当前格
            //    为锚点、自然自愈。⚠️ **不要**改成无条件 `this._selectTable()` —— 那会把多格选区
            //    塌成单格（用户会莫名其妙丢失选区）。
            if (this._selAnchor && !t.contains(this._selAnchor)) this._selAnchor = null;
            // ✅ 同理：本操作可能**造出了新格**（插行/插列/拆分都用 `createElement('td')`）——
            //    新格不带 `contenteditable`，不补挂就会「看得见、打不了字」。
            this._markTableEditable(t);
            this._tableSnapshots.push(snap);
        },

        /** 表内对齐（作用于**选中格**；空选区回落到单格）
         *
         * ⚠️ **不压快照**：spec §2.4 的结构操作清单是「合并/拆分/插删行列/**删图**」——
         *    对齐与 B/I/S **不在其中**，属「格内文字级」操作，**明确不纳入撤销**
         *    （TODOS T42 第 3 条已登记）。下面 `tableExec` 同。 */
        tableAlign(v) {
            const t = this._tableEl();
            if (!t) return;
            const cells = [...t.querySelectorAll('.rvb-cell-sel')];
            window.tableEditor.applyAlign(cells.length ? cells : [t.querySelector('td,th')], v);
        },

        /** 表内 B/I/S：与行编辑器同一路径（`@mousedown.prevent` 保选区）。
         *  ⚠️ contenteditable 的格子失焦会让选区塌掉 ⇒ 模板上必须写 `@mousedown.prevent`。
         *  ⚠️ 同 `tableAlign`：**不压快照**（文字级操作不纳入撤销）。 */
        tableExec(cmd) {
            const t = this._tableEl();
            if (!t) return;
            const sel = window.getSelection();
            if (!sel || !sel.rangeCount) return;
            document.execCommand(cmd, false, null);
        },

        /** 删图（作用于选中格；空选区回落到单格）
         *
         * ⚠️ **快照必须压在操作「前」**（spec §2.4 把「删图」列进结构操作，并明写「**前** push 一次」）：
         *    `tableUndo` 的口径是「还原被 `pop()` 掉的那个值」，压在**后**就会 restore 回**当前**状态
         *    ⇒ 点一次「撤销」**空转**（表面像没反应）。这与 `tableOp` 的「操作前压」是同一口径。
         *    ⚠️ `t.outerHTML` 必须**在** `removeImages` 之前求值 —— 写成 `const snap = t.outerHTML`
         *    再删是正确顺序，**不要**图省事写成删完再读。 */
        tableRemoveImages() {
            const t = this._tableEl();
            if (!t) return;
            const cells = [...t.querySelectorAll('.rvb-cell-sel')];
            const snap = t.outerHTML;                       // ← 操作前
            const n = window.tableEditor.removeImages(cells.length ? cells : [t.querySelector('td,th')]);
            if (n) this._tableSnapshots.push(snap);
            this.tableError = n ? '' : '选中的格子里没有图片';
        },

        /** 撤销：弹出最近一次快照并**还原被弹出的那一个**（spec §2.4；只覆盖结构操作，不覆盖格内文字）。
         *
         * ⚠️ **必须 restore 被 `pop()` 掉的值，不能 restore「新栈顶」** —— `tableOp` 是**操作前**
         *    压快照，故 op2 之后栈是 `[D0, D1]`；restore 新栈顶会得到 D0 ⇒ **一次撤销回退两步**
         *    （Task 3 评审复核时指出的 off-by-one）。被 pop 的 D1 才是「op2 之前的状态」。
         *
         * ⚠️ **栈语义（写死，勿"优化"）**：`enterEdit` 压入的栈首是**基线**；`tableOp` 每次成功后
         *    压入**操作前**状态。因 op1 的操作前状态 == 基线，栈里会出现两项相同值 —— 那**不是冗余**：
         *    配合「还原被 pop 的值」，它恰好充当「已无可撤销」的哨兵（`length <= 1` 即到底）。
         *    Task 4 评审曾按旧口径（还原新栈顶）判其为「多余的空撤销」，那个判断在旧口径下成立、
         *    在当前口径下不成立。 */
        tableUndo() {
            const snaps = this._tableSnapshots || [];
            if (snaps.length <= 1) return;      // 只剩起点 ⇒ 没有可撤销的结构操作
            this._restoreTable(snaps.pop());    // ← 还原被弹出的那个
            this.tableError = '';
        },

        /** 用快照整体还原表格（结构操作回滚 / 撤销共用）。
         *  Task 4 只做「还原」；Task 5 在末尾重新绑定选中格。 */
        _restoreTable(snap) {
            const host = this._tableHost();
            const t = window.tableEditor.parse(snap);
            if (!host || !t) return;
            this._markTableEditable(t);       // 同 `enterEdit`：还原出来的格也要可编辑
            host.innerHTML = '';
            host.appendChild(t);
            // ⚠️ 旧锚点此刻已**分离**（指向被替换掉的旧表）⇒ 必须重置，否则 `selectRange` 里
            //    `anchor.closest('tr')` 为 null、`rows.indexOf(null)` = -1 ⇒ `g.grid[-1]` 抛错。
            this._selAnchor = null;
            this._selectTable();
        },

        _resultScroller() {
            const box = document.getElementById('rvb-result');
            for (let s = box && box.parentElement; s; s = s.parentElement) {
                if (s.scrollHeight > s.clientHeight) return s;
            }
            return null;
        },
    };
}
