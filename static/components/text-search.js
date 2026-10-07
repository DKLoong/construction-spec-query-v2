/* 跨块搜索的共享底层：搜索词→正则 / 计数 / DOM 标记与撤标记（P4 阶段二，Task 8）
 *
 * 为什么单独成文件：本仓没有 JS 单测基建（无 package.json），**探针是唯一的 JS 测试宿主**
 * —— 纯函数才能被 page.evaluate 逐条断言。模块形态与 md-inline.js / table-editor.js 一致。
 *
 * 范围：**只做可复用的底层**。工具条接线、计数展示、上一处/下一处、替换是 Task 9/10 的事。
 * 来源：旧源码审查页（partials/ocr_review.html）里的三段可复用逻辑被抽到这里 ——
 * `_queryRegex` / `highlightInPreview` / `clearHighlight`。该页**一个字符都没动**
 * （它有自己的探针组 f11 守着），所以这里是「新副本」而那一份仍原样存在。
 *
 * 为什么旧页那套模型搬不过来：版面块审查页（review-blocks.js）**没有 textarea**，
 * 搜索绑不到「源码偏移」上；本模块只承诺「给一个 DOM 根 + 一个搜索词」，与页面结构无关。
 *
 * 硬约束（每条都有事故依据，别绕开）：
 * - 切分只能用正则 `exec`，**不能用 `String.split`**：split 只在正则**带捕获组**时才把
 *   命中片段一并返回，靠它区分「命中片段 / 间隔文本」会随正则写法**静默错位**（本仓踩过）。
 * - `m[0] === ''` 必须 `lastIndex++` 后 continue —— 防零宽匹配死循环。
 * - 只处理**文本节点**、不跨元素边界：跨 <strong> 或跨表格单元格的匹配**不高亮**。
 *   做精确对位需要维护一张「源码偏移 ↔ DOM 文本节点」映射表，收益不抵复杂度。
 *   但**计数**照旧按字符串统计（countIn）—— 「计数域 ≠ 高亮域」是**已知且刻意**的取舍。
 * - clearMarks 必须 `parent.normalize()` 合并相邻文本节点：不合并的话反复搜索会攒下
 *   一堆碎片文本节点（旧页注释记过）。
 * - 标记只作用在**已渲染的 DOM** 上，**绝不改 Markdown 再重渲染**：OCR 的表格是**原始 HTML**，
 *   整篇 HTML 转义会把 `<table>` 打成字面文本 —— 旧页实测过「每敲一个字符预览里的表格就塌一次」。
 * - markInto 自己先 clearMarks(root)（幂等）：反复搜同一根不会把 <mark> 再包一层，
 *   也保证返回的计数是**本次**的。
 * - 零依赖：除 `document` / `NodeFilter` 外不依赖任何全局。`queryRegex` / `countIn`
 *   是**纯字符串运算**，不碰 DOM ⇒ 能被 node / page.evaluate 直接断言。
 */
(function () {
    'use strict';

    const MARK_CLASS = 'search-highlight';
    const SEL_MARK = 'mark.' + MARK_CLASS;
    // 正则元字符转义（单一来源：计数、标记、撤标记、定位共用同一份口径，避免三处各写一遍而漂移）
    const META = /[.*+?^${}()|[\]\\]/g;

    /** 搜索词 → 正则的**单一来源**。元字符一律转义（搜 "a.b" 不得匹配 "axb"）。
     *  `q` 为空串或非字符串 ⇒ `null`（调用方据此短路，不要自己判空）。 */
    function queryRegex(q, flags) {
        if (typeof q !== 'string' || q === '') return null;
        return new RegExp(q.replace(META, '\\$&'), flags);
    }

    /** 一段文本里的命中数（纯字符串运算，不碰 DOM）。大小写不敏感，与高亮同一口径。 */
    function countIn(text, q) {
        if (typeof text !== 'string') return 0;
        const rx = queryRegex(q, 'gi');
        if (!rx) return 0;
        let n = 0;
        let m;
        while ((m = rx.exec(text)) !== null) {
            if (m[0] === '') { rx.lastIndex++; continue; }   // 防零宽匹配死循环
            n++;
        }
        return n;
    }

    /** 撤标记：把 `<mark class="search-highlight">` 换回文本节点并合并相邻文本节点。
     *  幂等 —— 根里没有标记时是空转。 */
    function clearMarks(root) {
        if (!root || !root.querySelectorAll) return;
        for (const mark of root.querySelectorAll(SEL_MARK)) {
            const parent = mark.parentNode;
            if (!parent) continue;
            parent.replaceChild(document.createTextNode(mark.textContent), mark);
            parent.normalize();   // 不合并的话反复搜索会攒下一堆碎片文本节点
        }
    }

    /** 在 `root` 的**文本节点**里插 `<mark class="search-highlight">`，返回本次插入的标记数。
     *  先 clearMarks(root) 再标（幂等，且保证计数是本次的）。跨元素边界的匹配不高亮。 */
    function markInto(root, q) {
        if (!root) return 0;
        clearMarks(root);
        const probe = queryRegex(q, 'i');   // 不带 g：test() 不会推进 lastIndex
        if (!probe) return 0;

        // 第一遍只挑出「含命中」的文本节点，第二遍才改 DOM：
        // 边遍历边替换会让 TreeWalker 走到刚插进去的节点上。
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const hits = [];
        let node;
        while ((node = walker.nextNode())) {
            if (node.nodeValue && probe.test(node.nodeValue)) hits.push(node);
        }

        let total = 0;
        for (const textNode of hits) {
            const text = textNode.nodeValue;
            const rx = queryRegex(q, 'gi');
            const frag = document.createDocumentFragment();
            let last = 0;
            let m;
            while ((m = rx.exec(text)) !== null) {
                if (m[0] === '') { rx.lastIndex++; continue; }   // 防零宽匹配死循环
                if (m.index > last) {
                    frag.appendChild(document.createTextNode(text.slice(last, m.index)));
                }
                const mark = document.createElement('mark');
                mark.className = MARK_CLASS;
                mark.textContent = m[0];
                frag.appendChild(mark);
                last = m.index + m[0].length;
                total++;
            }
            if (last < text.length) {
                frag.appendChild(document.createTextNode(text.slice(last)));
            }
            textNode.parentNode.replaceChild(frag, textNode);
        }
        return total;
    }

    /** 第 `nth` 个（0 起）属于搜索词 `q` 的 `<mark>`，供「当前项」样式与滚动定位；越界/无词 ⇒ null。
     *  只用 `q` 判空 + 过滤标记：`markInto` 之外留在 DOM 里的旧标记（改词后未重标）不会被误取。 */
    function markAt(root, q, nth) {
        if (!root || !root.querySelectorAll) return null;
        const probe = queryRegex(q, 'i');
        if (!probe) return null;
        const n = Number(nth);
        if (!Number.isFinite(n) || n < 0) return null;
        let seen = 0;
        for (const mark of root.querySelectorAll(SEL_MARK)) {
            if (!probe.test(mark.textContent || '')) continue;
            if (seen === n) return mark;
            seen++;
        }
        return null;
    }

    window.textSearch = {
        MARK_CLASS: MARK_CLASS,
        queryRegex: queryRegex,
        countIn: countIn,
        markInto: markInto,
        clearMarks: clearMarks,
        markAt: markAt,
    };
})();
