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
