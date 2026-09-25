// 条文详情弹窗 — 纯 JS 实现，不依赖 Alpine 可见性控制
//
// 翻页上下文：打开方带上来源列表（ids 序列 + index），弹窗底部据此显示
// 「上一条 / 当前-总数 / 下一条」；不带（或 id 不在列表里）则整条隐藏，
// 保持「单条打开」的原行为。
//
// 打开的两种方式：
//   1. window.dispatchEvent(new CustomEvent('view-clause', {detail: {id, ids, index}}))
//   2. 点击任意带 data-clause-id 的元素 —— 在最近的 [data-clause-ids] 容器内定位上下文
(function () {
    'use strict';

    const OVERLAY_ID = 'clause-modal-overlay';
    const CONTENT_ID = 'clause-modal-content';
    const LOADING_CLASS = 'clause-modal-loading';
    const FOOTER_ID = 'clause-modal-footer';
    const PREV_ID = 'clause-nav-prev';
    const NEXT_ID = 'clause-nav-next';
    const COUNT_ID = 'clause-nav-count';

    // 当前翻页上下文：ids = 来源列表的条文 id 序列，index = 当前条文下标（-1 = 不提供翻页）
    let nav = { ids: [], index: -1 };

    let overlay = null;
    let loadingEl = null;
    let contentEl = null;

    function getEls() {
        overlay = document.getElementById(OVERLAY_ID);
        if (overlay) {
            loadingEl = overlay.querySelector('.' + LOADING_CLASS);
            contentEl = document.getElementById(CONTENT_ID);
        }
    }

    // 归一化上下文：index 必须与 id 自洽。调用方传错（或容器里没有该 id）时
    // 按 id 反查；仍查不到就放弃翻页，而不是显示一个错位的「1/N」。
    function normalizeNav(id, ctx) {
        if (!ctx || !Array.isArray(ctx.ids) || !ctx.ids.length) return { ids: [], index: -1 };
        const ids = ctx.ids.slice();
        const index = (typeof ctx.index === 'number' && ids[ctx.index] === id)
            ? ctx.index
            : ids.indexOf(id);
        if (index < 0) return { ids: [], index: -1 };
        return { ids: ids, index: index };
    }

    // 翻页条渲染：无上下文整条隐藏；首/末条禁用对应按钮
    function renderNav() {
        const footer = document.getElementById(FOOTER_ID);
        if (!footer) return;
        if (!nav.ids.length || nav.index < 0) {
            footer.style.display = 'none';
            return;
        }
        footer.style.display = 'flex';
        const prev = document.getElementById(PREV_ID);
        const next = document.getElementById(NEXT_ID);
        const count = document.getElementById(COUNT_ID);
        if (count) count.textContent = (nav.index + 1) + '/' + nav.ids.length;
        if (prev) prev.disabled = nav.index <= 0;
        if (next) next.disabled = nav.index >= nav.ids.length - 1;
    }

    function show(id, ctx) {
        getEls();
        if (!overlay) return;

        nav = normalizeNav(id, ctx);
        renderNav();

        overlay.style.display = 'flex';
        if (loadingEl) loadingEl.style.display = 'block';

        // 通过 HTMX 加载条文详情
        htmx.ajax('GET', '/clause/' + id, {
            target: '#' + CONTENT_ID,
            swap: 'innerHTML'
        });

        // 内容加载完成后隐藏加载状态
        if (contentEl) {
            contentEl.addEventListener('htmx:afterSettle', function onSettle() {
                if (loadingEl) loadingEl.style.display = 'none';
                contentEl.removeEventListener('htmx:afterSettle', onSettle);
            });
        }
    }

    // 翻页：越界直接忽略（按钮此时也是 disabled，双保险）
    function go(delta) {
        const target = nav.index + delta;
        if (target < 0 || target >= nav.ids.length) return;
        show(nav.ids[target], { ids: nav.ids, index: target });
    }

    function hide() {
        getEls();
        if (!overlay) return;
        overlay.style.display = 'none';
        if (loadingEl) loadingEl.style.display = 'none';
        if (contentEl) contentEl.innerHTML = '';
        nav = { ids: [], index: -1 };
        renderNav();
    }

    // 从带 data-clause-id 的元素打开：在最近的 data-clause-ids 容器里定位翻页上下文
    function openFrom(el) {
        const id = parseInt(el.getAttribute('data-clause-id'), 10);
        if (!id) return;
        const holder = el.closest('[data-clause-ids]');
        let ids = [];
        if (holder) {
            try {
                ids = JSON.parse(holder.getAttribute('data-clause-ids') || '[]');
            } catch (e) {
                ids = [];
            }
        }
        show(id, { ids: ids, index: ids.indexOf(id) });
    }

    // 初始化：监听事件 + 键盘/点击关闭
    function init() {
        getEls();

        // 监听搜索结果/条文列表的显式派发（detail 可带 ids/index）
        window.addEventListener('view-clause', function (e) {
            if (e.detail && e.detail.id) show(e.detail.id, e.detail);
        });

        // document 级委托：任何带 data-clause-id 的元素点击即打开详情。
        // 用委托而非逐个绑定，htmx 换入的新元素（翻页、行级 swap）无需重新绑定。
        document.addEventListener('click', function (e) {
            if (!e.target || !e.target.closest) return;
            const el = e.target.closest('[data-clause-id]');
            if (el) openFrom(el);
        });

        const prev = document.getElementById(PREV_ID);
        const next = document.getElementById(NEXT_ID);
        if (prev) prev.addEventListener('click', function () { go(-1); });
        if (next) next.addEventListener('click', function () { go(1); });

        if (!overlay) return;

        // ESC 关闭
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && overlay.style.display === 'flex') {
                hide();
            }
        });

        // 点击遮罩关闭
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) hide();
        });
    }

    // 在 DOM 加载完成后初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 导出 hide 以供 Alpine 组件（loading 指示器）调用
    window.closeClauseModal = hide;
})();
