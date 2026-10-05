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
        editableLabels: [],    // 后端下发的可编辑 label 白名单（单一来源，不在此另写一份）
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
        _previewTimer: null,
        _dragging: false,
        _dragFrom: null,

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
                data = await resp.json();
            } catch (e) {
                this.error = '版面数据加载失败：' + e;
                return;
            }
            this.pages = data.pages || [];
            this.editableLabels = data.editable_labels || [];
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
                return r.ok ? ((await r.json()).file_name || '') : '';
            } catch (e) {
                return '';
            }
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
                return window.mdRender.renderHtml(this.contentOf(b),
                    '/import/review/' + this.taskId + '/');
            } catch (e) {
                return '<p style="color:#c00">渲染错误</p>';
            }
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

        _scrollRightTo(idx) {
            const list = this.$refs.list;
            this._scrollInto(list && list.querySelector(`[data-block-idx="${idx}"]`),
                             list, 'nearest');
        },

        // ── P2：行级编辑（编辑**只写内存** this.edits，不发任何写请求） ──

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
            this.schedulePreview();          // 保存后刷新切分预览（防抖）
        },

        cancelEdit() {
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
                if (!cr.ok) throw new Error((await cr.json()).detail || ('compose ' + cr.status));
                const md = (await cr.json()).md_text;
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

        _resultScroller() {
            const box = document.getElementById('rvb-result');
            for (let s = box && box.parentElement; s; s = s.parentElement) {
                if (s.scrollHeight > s.clientHeight) return s;
            }
            return null;
        },
    };
}
