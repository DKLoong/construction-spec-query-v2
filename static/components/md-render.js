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
                    ],
                    // 无法解析的公式保持原样文本，不影响正文展示
                    throwOnError: false,
                });
            } catch (e) {
                /* 公式渲染异常不阻断 markdown 正文 */
            }
        }
    }

    // 把 markdown 渲染进指定容器（marked → DOMPurify → KaTeX）
    function renderInto(el, md, baseUrl) {
        if (!el) return;
        let raw = fixOrphanSup(rewriteImg(md || '', baseUrl));
        let html;
        try {
            html = window.marked.parse(raw, { gfm: true, breaks: true });
        } catch (e) {
            html = String(raw).replace(/</g, '&lt;');
        }
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html);
        }
        el.innerHTML = html;
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
        katexize: katexize,
        renderInto: renderInto,
        renderHtml: renderHtml,
        renderSearchResults: renderSearchResults,
    };
})();
