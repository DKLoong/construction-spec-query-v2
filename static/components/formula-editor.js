/* 公式编辑器的纯函数层（OCR 审查页 P4）
 *
 * 为什么单独成文件：本仓没有 JS 测试设施（无 package.json），**探针是唯一的 JS 测试宿主**
 * —— 只有纯函数才能被 `page.evaluate` 逐条单元式断言。故本文件除 `renderPreview` 外
 * **一律不碰 DOM**（`validate` 只做字符串 + KaTeX，不建节点）。
 * 由 Task 3（正文公式就地编辑）/ Task 5（表格内公式弹窗）消费，模块形态与
 * `table-editor.js` / `md-inline.js` 一致（IIFE + 挂 window）。
 *
 * 关键约束（每条都有实测依据，见计划 §P4 公式线）：
 *   - `parseLine` **锚定整行**且要求定界符外只有空白 —— 真实语料里「正文行含 `$…$`」大量存在
 *     （实测某产物有 177 个行内公式片段），只按「行里有没有 `$…$`」判会把这些正文行误判成公式行。
 *   - `validate` 必须用**原始 LaTeX**，**不得**先过 `md-render.js` 的 `fixOrphanSup` /
 *     `protectTildes`：`protectTildes` 的 `/(?<!~)~(?![~])/g` 对整篇 md 生效，会把 `$$…$$`
 *     内部的 LaTeX `~`（不断行空格）换成私有区字符 U+E000 ⇒ **合法公式被验成非法**
 *     （实测 `data/outputs/7c5b6ac9/7c5b6ac9.md` 第 34 个块级公式写作 `…\mathrm{~}…`）。
 *   - KaTeX 选项保持默认、**绝不打开 `trust`**（默认 `trust:false`，`\href` / `\includegraphics`
 *     之类不被放行）；打开等于给出一个 XSS 注入面（spec §14）。
 *   - **不要复用** `window.mdRender.katexize(root)` 判合法性：它作用在 DOM 容器上，且
 *     `throwOnError:false`（非法公式静默保留原文，既不抛也不返回布尔），判不了真假。
 *
 * 空公式口径：`tex` 去空白后为空（如 `$$$$`）**不算公式** —— `parseLine` 返回 null、
 * `findInText` 返回 null、`validate` 返回 `{ok:false, error:'公式内容为空'}`，
 * 即「空公式进不了编辑态、也存不回去」。
 */
(function () {
    'use strict';

    // ⚠️ `$` 按正文口径**不是**段落分隔符，`.` 也不会跨行匹配——一律不用 `.`，用 `[\s\S]`。
    //    两个定界符并列时 `$$` 必须优先，否则 `$$x$$` 会被 `$` 抢成 `$` 的错切（见 findInText）。
    const LINE_RE = /^([\s\S]*?)(\$\$[\s\S]*?\$\$|\$[\s\S]*?\$)([\s\S]*)$/;

    /** KaTeX 报错是**多行**的（头一行 + 带上下文的源码回显），整段塞进界面很难看 ⇒ 取首个非空行。 */
    function _firstLine(msg) {
        const lines = String(msg == null ? '' : msg).split('\n');
        for (let i = 0; i < lines.length; i++) {
            const t = lines[i].trim();
            if (t) return t;
        }
        return '公式无法解析';
    }

    /** 严格模式渲染，返回 `{ok, html}` 或 `{ok:false, error}`。**不碰页面 DOM**。 */
    function _render(tex, display) {
        const t = String(tex == null ? '' : tex);
        if (t.trim() === '') return { ok: false, error: '公式内容为空' };
        try {
            // 严格模式（throwOnError:true）：非法公式**必须抛**，否则无法判合法性。
            // 其余选项一概保持默认 —— 尤其不设 `trust`（spec §14 的安全要求）。
            return { ok: true, html: window.katex.renderToString(t, { throwOnError: true, displayMode: !!display }) };
        } catch (e) {
            return { ok: false, error: _firstLine(e && e.message != null ? e.message : e) };
        }
    }

    /** 拆一行公式。`lead`/`tail` 是**原样的**首尾空白（组行时要逐字节还回去）。
     *  不是公式行返回 null：`$…$` 只是**行的一部分**（正文里的行内公式）时同样返回 null。 */
    function parseLine(mdLine) {
        const s = String(mdLine == null ? '' : mdLine);
        const m = LINE_RE.exec(s);
        if (!m) return null;
        const lead = m[1], body = m[2], tail = m[3];
        // 定界符之外只允许空白 —— 这是「正文行含 $…$ 不算公式行」的唯一抓手
        if (lead.trim() !== '' || tail.trim() !== '') return null;
        const display = body.slice(0, 2) === '$$';
        const tex = display ? body.slice(2, -2) : body.slice(1, -1);
        if (tex.trim() === '') return null;                 // `$$$$` / `$ $` 不是公式
        return { lead: lead, tex: tex, tail: tail, display: display };
    }

    /** 组回一行：**保留 `srcLine` 的首尾空白**（缩进、行尾空格都还回去），只换公式体。
     *  `srcLine` 解析不出公式（空串 / 已不是公式行）⇒ 回落成独立一行的 ` $$ tex $$ ` /
     *  ` $ tex $ `（前后各一个空格，往正文里插时不会与相邻文字粘连）。 */
    function buildLine(tex, display, srcLine) {
        const t = String(tex == null ? '' : tex);
        const body = display ? '$$' + t + '$$' : '$' + t + '$';
        const p = parseLine(srcLine);
        if (p) return p.lead + body + p.tail;
        return ' ' + body + ' ';
    }

    /** KaTeX 严格模式校验。成功 `{ok:true, error:''}`；失败 `{ok:false, error:<首个非空行>}`。 */
    function validate(tex, display) {
        const r = _render(tex, display);
        return r.ok ? { ok: true, error: '' } : { ok: false, error: r.error };
    }

    /** 在**任意文本**（表格格内文字）里定位第一处公式。
     *  `$$…$$` **优先**：两者并存时若不优先，`$$x$$` 会被 `$` 先匹配成 `$` 的错切。
     *  区间是 `[start, end)`（即 `text.slice(start, end)` 恰为含定界符的原文）。
     *  找不到 / 只有空公式 ⇒ null。 */
    function findInText(text) {
        const s = String(text == null ? '' : text);
        // ⚠️ 带 `/g` 的正则该字面量必须**在函数内新建**：`RegExp.lastIndex` 是对象级状态，
        //    提到模块级复用会让上一次调用的残留下标吃掉下一次的首个匹配。
        const scan = (re, display) => {
            re.lastIndex = 0;
            let m;
            while ((m = re.exec(s)) !== null) {
                if (m[1].trim() === '') continue;           // 空公式不选（如文本里的 `$$$$`）
                return { start: m.index, end: m.index + m[0].length, tex: m[1], display: display };
            }
            return null;
        };
        return scan(/\$\$([\s\S]*?)\$\$/g, true) || scan(/\$([\s\S]*?)\$/g, false);
    }

    /** 把渲染结果写进预览宿主 `host`；非法时 `host` 显示错误文案而**不抛异常**。
     *  返回与 `validate` 同形（`{ok, error}`）。 */
    function renderPreview(host, tex, display) {
        if (!host) return { ok: false, error: '预览宿主不存在' };
        const r = _render(tex, display);
        if (!r.ok) {
            // ⚠️ 错误文案里含**用户输入的原文**（KaTeX 会把它回显进 message）⇒ 必须走
            //    `textContent`，拼 `innerHTML` 就是一个自伤型 XSS 面（spec §14）。
            host.textContent = r.error;
            return { ok: false, error: r.error };
        }
        host.innerHTML = r.html;
        return { ok: true, error: '' };
    }

    window.formulaEditor = {
        parseLine: parseLine,
        buildLine: buildLine,
        validate: validate,
        findInText: findInText,
        renderPreview: renderPreview,
    };
})();
