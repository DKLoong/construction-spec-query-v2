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
            el._rvbBaseline = el.outerHTML;
            // ⚠️ `_rvbBaseline` 是**表级**、`_rvbSource` 必须是**输入级且恰好覆盖这张表** ——
            //    两者作用域一旦不一致，未改动时 `serialize` 会把整个入参串当块内容返回
            //    （静默写坏 md，且往返一致型断言对它恒绿、抓不到）。
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
        // 先克隆再清洗：直接在活 DOM 上剥属性会把编辑器自身的 contenteditable/选中态
        // 一起剥掉，编辑态当场失效。
        const html = _scrub(tableEl.cloneNode(true)).outerHTML;
        // scrub 后仍等于 parse 时的归一化基线 ⇒ 用户没改动 ⇒ 返回原始串（逐字节不变）。
        // `_rvbSource` 可能**故意没被缓存**（入参不是单张表，见 parse 的守卫）⇒ 必须一并判空，
        // 否则会返回 undefined 而非归一化 HTML。
        if (tableEl._rvbSource !== undefined && html === tableEl._rvbBaseline) {
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
        const tr = refCell.closest('tr');
        const r = [...tableEl.querySelectorAll('tr')].indexOf(tr);
        const c = g.grid[r].indexOf(refCell);
        if (c < 0) return false;
        const at = before ? c : c + 1;
        // ⚠️ **必须用 `===`**：「边界落在某个跨列格内部」⇔ 边界两侧是**同一个**合并格
        //    （`row[at] === row[at-1]`）。计划原文此处写成 `!==` 是**反向**的 —— 对任意
        //    无合并的普通表，边界两侧恒为不同格 ⇒ `some` 恒真 ⇒ insertCol **永远拒绝**、
        //    一个格也不插（实测：`insertCol(t, firstTd, false)` 返回 false、行仍 3 列，
        //    从而 f14_9 要求的 `cols1 == 4` 不可达）。用 `===` 后：普通表放行、边界真正
        //    落在合并格内部（如 `colspan=2` 的第 1~2 列之间）时才拒绝 —— 与下方注释语义一致。
        if (g.grid.some((row) => row[at] && row[at] === row[at - 1])) {
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

    window.tableEditor = { parse, serialize, validate,
                           insertRow, insertCol, deleteRows, deleteCols };
})();
