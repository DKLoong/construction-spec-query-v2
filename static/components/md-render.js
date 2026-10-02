// 统一 markdown 渲染：marked → DOMPurify → KaTeX（公式/表格/图表正常显示）
// 数据库仍存原始 markdown，页面渲染时转成可读排版。
// 用法：
//   window.mdRender.renderInto(el, md, baseUrl)  // 渲染进容器
//   window.mdRender.renderHtml(md, baseUrl)      // 返回 HTML 字符串
//   window.mdRender.renderSearchResults()        // 扫描 .clause-content[data-md] 批量渲染
(function () {
    'use strict';

    // OCR 结果里常见的孤立上标公式（缺基数，如 "$ ^{2} $"）在 KaTeX 下会报错，
    // 预处理为 ${}^{...}$ 使其渲染为普通上标（如 ²）
    function fixOrphanSup(md) {
        return md.replace(/\$(\s*)\^\{([^{}]+)\}(\s*)\$/g, function (m, a, body, b) {
            return '$ ' + a + '{}^{' + body + '}' + b + ' $';
        });
    }

    // 规范正文里的 `~` 是**区间号**（5~10~20、1.10~1.25、6~8），不是删除线定界符。
    // 但 marked v15 的 GFM del 规则写作 `(~~?)` —— 第二个波浪线**可选**，
    // 于是单个 ~ 成对出现就被吃成 <del>（实测库内 153 条 content 含 ~，11 条会画线）。
    //
    // 处理：把**孤立**的 ~（非 ~~ 删除线的成员）先换成私有区哨兵交给 marked，
    // 渲染后再还原；`~~…~~` 因此照旧工作。
    // 为什么不用反斜杠转义（`\~`）：marked 对**原始 HTML 块**（PaddleOCR 的 <table>）
    // 整体透传、不做行内解析，`\~` 会原样留在表格里显示成「\~」。
    var TILDE_SENTINEL = String.fromCharCode(0xE000);   // 私有区字符，正文不会出现
    var LONE_TILDE = /(?<!~)~(?![~])/g;

    function protectTildes(md) {
        return String(md == null ? '' : md).replace(LONE_TILDE, TILDE_SENTINEL);
    }

    function restoreTildes(html) {
        return String(html == null ? '' : html).split(TILDE_SENTINEL).join('~');
    }

    // 改写相对图片路径 imgs/xxx → baseUrl + imgs/xxx
    function rewriteImg(md, baseUrl) {
        if (!baseUrl) return md;
        return md
            .replace(/(src=["'])imgs\//g, '$1' + baseUrl + 'imgs/')
            .replace(/!\[([^\]]*)\]\(imgs\//g, '![$1](' + baseUrl + 'imgs/');
    }

    // 容器内的 $...$ / $$...$$ 公式渲染为 KaTeX
    function katexize(root) {
        if (window.renderMathInElement) {
            try {
                window.renderMathInElement(root, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '$', right: '$', display: false },
                        // 兼容标准 LaTeX 行内公式 \(...\)（AI 归纳可能用此格式）
                        { left: '\\(', right: '\\)', display: false },
                    ],
                    // 无法解析的公式保持原样文本，不影响正文展示
                    throwOnError: false,
                    // 数学模式中的中文（如 "$重=50kg$"）经 cjk_fallback 渲染正常，
                    // 仅因 strict 默认 warn 而刷控制台 unicodeTextInMathMode 警告。
                    // 只忽略这一类，其余 strict 检查（如 href 注入等）保持 warn。
                    strict: function (errorCode) {
                        return errorCode === 'unicodeTextInMathMode' ? 'ignore' : 'warn';
                    },
                });
            } catch (e) {
                /* 公式渲染异常不阻断 markdown 正文 */
            }
        }
    }

    // 把 markdown 渲染进指定容器（marked → DOMPurify → KaTeX）
    function renderInto(el, md, baseUrl) {
        if (!el) return;
        // PaddleOCR 输出的表格单元格内用字面 \n（反斜杠+n 两字符）表示换行。
        // 必须替换为 <br> 而非真实换行——HTML 单元格内的真实换行会被浏览器
        // 空白折叠成空格，无法达到换行效果。
        let raw = fixOrphanSup(rewriteImg(protectTildes(md), baseUrl)).replace(/\\n/g, '<br>');
        // 兼容标准 LaTeX 行内公式 \(...\)：marked 会把 \( 当转义吃掉反斜杠，
        // 故在 marked 之前统一转换为 $...$（KaTeX 只认 $ 定界符）
        raw = raw.replace(/\\\(/g, '$').replace(/\\\)/g, '$');
        let html;
        try {
            html = window.marked.parse(raw, { gfm: true, breaks: true });
        } catch (e) {
            html = String(raw).replace(/</g, '&lt;');
        }
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html);
        }
        // 还原哨兵必须早于 katexize：KaTeX 拿到哨兵会渲染失败，
        // 且它若进入公式文本，还原后再扫描就晚了（katexize 已把公式换成 DOM 节点）
        el.innerHTML = restoreTildes(html);
        katexize(el);
    }

    // 返回渲染后的 HTML 字符串（含 KaTeX 转换结果）
    function renderHtml(md, baseUrl) {
        var el = document.createElement('div');
        renderInto(el, md, baseUrl);
        return el.innerHTML;
    }

    // 批量渲染搜索结果列表：扫描 .clause-content[data-md]，dataset.md 为原始 md 直接渲染
    function renderSearchResults() {
        var nodes = document.querySelectorAll('.clause-content[data-md]');
        for (var i = 0; i < nodes.length; i++) {
            try {
                var md = nodes[i].getAttribute('data-md') || '';
                renderInto(nodes[i], md, '');
            } catch (e) {
                /* 单条摘要渲染失败不影响其他条目 */
            }
        }
    }

    // 自动渲染搜索结果列表：
    // - 整页加载：DOM 就绪时扫描（本文件在 body 底部，早于内容区脚本执行）
    // - htmx 翻页/筛选：afterSwap 后新内容重新扫描
    function boot() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', renderSearchResults);
        } else {
            renderSearchResults();
        }
        if (window.htmx) {
            document.addEventListener('htmx:afterSwap', renderSearchResults);
        }
    }
    boot();

    window.mdRender = {
        fixOrphanSup: fixOrphanSup,
        rewriteImg: rewriteImg,
        protectTildes: protectTildes,
        restoreTildes: restoreTildes,
        katexize: katexize,
        renderInto: renderInto,
        renderHtml: renderHtml,
        renderSearchResults: renderSearchResults,
    };
})();
