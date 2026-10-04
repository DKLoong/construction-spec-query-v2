/* OCR 审查页（版面块 · P1 只读对照）
 *
 * 依赖：md-render.js —— 块内容统一走 window.mdRender.renderHtml，
 *       切勿另起 marked/DOMPurify 内联渲染路径（本仓既定约定）。
 * 后端契约：
 *   GET /import/review/{task_id}/layout            → {blocks, pages, stats}
 *   GET /import/review/{task_id}/page/{n}.png?scale=  → image/png
 *   GET /import/review/{task_id}/content           → {content, file_name}
 *
 * 本期**只读**：本文件不得出现任何写请求。
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
            // __i = 原下标：过滤/排序后仍能定位回 blocks（联动与 P2 的 edits 都用它）
            this.blocks = (data.blocks || []).map((b, i) => Object.assign({}, b, { __i: i }));
            this.curPage = this.pages.length ? this.pages[0].index : 1;
            this.fileName = await this._fileName();
            this.$nextTick(() => this._applyFit());
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

        pageUrl(n) {
            const s = this.scaleMode === 'fit' ? this._fitScale() : this.scaleMode;
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
                return window.mdRender.renderHtml(
                    b.content || '', '/import/review/' + this.taskId + '/');
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

        _scrollLeftTo() {
            const vp = this.$refs.vp;
            const box = vp && vp.querySelector('.rvb-box--on');
            if (box) box.scrollIntoView({ block: 'center', behavior: 'auto' });
        },

        _scrollRightTo(idx) {
            const list = this.$refs.list;
            const el = list && list.querySelector(`[data-block-idx="${idx}"]`);
            if (el) el.scrollIntoView({ block: 'nearest', behavior: 'auto' });
        },
    };
}
