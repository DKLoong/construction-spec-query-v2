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
            // 记两个东西（挂在元素上，随元素生命周期走）：
            //   _rvbSource   = 传入的**原始串**（含单引号、无 tbody）
            //   _rvbBaseline = parse 出来的**归一化形态**（`el.outerHTML`）
            // 浏览器解析必然归一化（插 tbody / 属性引号统一 / 丢自闭合 `/>`），
            // DOM 往返做不到字节相等 ⇒ 缓存源串是「未改动 ⇒ 原样返回」的唯一途径。
            // 注：expando 不会被 cloneNode 复制，故 serialize 里用「clone 的 outerHTML」
            // 对比「原元素的 baseline」，不会被克隆污染。
            el._rvbSource = src;
            el._rvbBaseline = el.outerHTML;
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
        // 先克隆再清洗：直接在活 DOM 上剥属性会把编辑器自身的 contenteditable/选中态
        // 一起剥掉，编辑态当场失效。
        const html = _scrub(tableEl.cloneNode(true)).outerHTML;
        // scrub 后仍等于 parse 时的归一化基线 ⇒ 用户没改动 ⇒ 返回原始串（逐字节不变）。
        if (tableEl._rvbBaseline !== undefined && html === tableEl._rvbBaseline) {
            return tableEl._rvbSource;
        }
        return html;
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
