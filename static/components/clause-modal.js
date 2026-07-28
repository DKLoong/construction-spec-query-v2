// 条文详情弹窗 — 纯 JS 实现，不依赖 Alpine 可见性控制
(function () {
    'use strict';

    const OVERLAY_ID = 'clause-modal-overlay';
    const CONTENT_ID = 'clause-modal-content';
    const LOADING_CLASS = 'clause-modal-loading';

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

    function show(id) {
        console.log('[clause-modal] show() called with id=' + id);
        getEls();
        if (!overlay) {
            console.warn('[clause-modal] overlay not found! id=' + OVERLAY_ID);
            return;
        }

        console.log('[clause-modal] overlay found, setting display=flex');
        overlay.style.display = 'flex';
        // 强制回流，确保浏览器完成布局计算
        void overlay.offsetHeight;
        void getComputedStyle(overlay).transform;
        // 诊断：检查内联样式 vs 计算后样式（如果 computed 是 none，说明有 !important 覆盖）
        console.log('[clause-modal] inline display:', overlay.style.display);
        console.log('[clause-modal] computed display:', getComputedStyle(overlay).display);
        // 诊断：位置/尺寸/z-index/opacity
        var cs = getComputedStyle(overlay);
        console.log('[clause-modal] z-index:', cs.zIndex, '| opacity:', cs.opacity, '| visibility:', cs.visibility);
        console.log('[clause-modal] top/right/bottom/left:', cs.top, cs.right, cs.bottom, cs.left);
        var rect = overlay.getBoundingClientRect();
        console.log('[clause-modal] rect:', JSON.stringify({x:rect.x, y:rect.y, w:rect.width, h:rect.height}));
        // 诊断：弹窗内的内容
        var box = overlay.querySelector('.clause-modal-box');
        if (box) {
            var bcs = getComputedStyle(box);
            var br = box.getBoundingClientRect();
            console.log('[clause-modal] box display:', bcs.display, '| rect:', JSON.stringify({x:br.x, y:br.y, w:br.width, h:br.height}));
        }

        // 显示加载中
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

    function hide() {
        getEls();
        if (!overlay) return;
        overlay.style.display = 'none';
        if (loadingEl) loadingEl.style.display = 'none';
        if (contentEl) contentEl.innerHTML = '';
    }

    // 初始化：监听事件 + 键盘/点击关闭
    function init() {
        console.log('[clause-modal] init() called');
        getEls();
        console.log('[clause-modal] overlay found:', !!overlay);

        // 监听搜索结果点击
        window.addEventListener('view-clause', function (e) {
            console.log('[clause-modal] view-clause event received, id=' + e.detail?.id);
            show(e.detail.id);
        });
        console.log('[clause-modal] listener registered');

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
