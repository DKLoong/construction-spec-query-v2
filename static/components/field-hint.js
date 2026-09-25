// 输入框聚焦悬浮提示 —— 全局单例，固定在触发元素**下方**显示，失焦即隐。
//
// 用法：给输入框加 data-hint="提示文案" 即可，无需在页面里写任何 JS 或弹层结构。
//
// 为什么用「全局单例 + position:fixed」而不是在每个输入框旁边放一个 absolute 的提示块：
//   条文分类编辑是在表格行内进行的，而表格外层的 .clause-table-wrapper 是
//   `max-height:55vh; overflow:auto` 的滚动容器——absolute 提示块一旦越过容器边界
//   就会被裁剪（聚焦最后一个输入框时几乎必然发生）。fixed 定位不受任何滚动容器影响。
//   代价是自己算位置，并跟随窗口滚动/缩放。
(function () {
    'use strict';

    const POP_ID = 'field-hint-pop';
    const OFFSET = 6;          // 与输入框底边的间距（px）

    let pop = null;
    let active = null;         // 当前触发提示的输入框

    function el() {
        if (!pop) pop = document.getElementById(POP_ID);
        return pop;
    }

    // 定位到触发元素正下方，并夹在视口内（右侧输入框的提示不越界）
    function place() {
        const box = el();
        if (!box || !active) return;
        const r = active.getBoundingClientRect();
        const w = box.offsetWidth;
        let left = r.left;
        const maxLeft = window.innerWidth - w - 8;
        if (left > maxLeft) left = Math.max(8, maxLeft);
        box.style.left = left + 'px';
        box.style.top = (r.bottom + OFFSET) + 'px';
    }

    function show(input) {
        const box = el();
        if (!box) return;
        const text = input.getAttribute('data-hint');
        if (!text) return;
        active = input;
        box.textContent = text;
        box.style.display = 'block';
        place();               // 先显示再定位：需要 offsetWidth 才能夹取右边界
    }

    function hide() {
        const box = el();
        active = null;
        if (box) box.style.display = 'none';
    }

    function init() {
        // 事件委托：htmx 换入的新输入框（分类编辑的行级 swap）无需重新绑定
        document.addEventListener('focusin', function (e) {
            const t = e.target;
            if (t && t.matches && t.matches('[data-hint]')) show(t);
        });
        document.addEventListener('focusout', function (e) {
            if (active && e.target === active) hide();
        });
        // 跟随滚动/缩放：滚动时若提示还开着就重算位置（而不是留在原地错位）
        window.addEventListener('scroll', function () { if (active) place(); }, true);
        window.addEventListener('resize', function () { if (active) place(); });
        // 兜底：弹窗/页面尺寸变化导致的失焦不一定触发 focusout
        document.addEventListener('visibilitychange', function () {
            if (document.hidden) hide();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
