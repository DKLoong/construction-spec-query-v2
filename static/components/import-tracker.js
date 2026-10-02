// 导入任务跨页追踪器（base.html 全局加载，任意页面存活）
//
// 背景：导入进度原来只活在**当前文档**的 DOM 里（#import-status 的 hx-get 轮询）。
// 导入弹窗用 x-show，所以关掉弹窗不销毁 DOM、轮询照跑 —— 「关掉还能回来看进度」
// 因此成立；但一旦整页跳转，文档销毁、task_id 无处可寻：服务端 progress_store
// 还在，客户端却再没有把手，于是实际体验是「导入期间不能干别的事」。
//
// 本脚本把 task_id 存 sessionStorage（切页/返回均不丢，per-tab，与 rebuild.js 同口径），
// 在任意页面恢复为一个**不打断操作**的常驻小浮标；终态自动撤下并弹一次轻提示。
//
// 与 rebuild.js 的两点差异：
//   1) 走 JSON 端点 /import/progress/{id}/json —— 浮标只要数字，不必解析 HTML；
//   2) review_needed **不是终态**：它要一直留到你审查或取消，故不清 task_id，
//      浮标转为「待审查」入口（否则用户切页后找不到那次导入的成果）。
(function () {
    'use strict';
    var KEY = 'importTaskId';
    var POLL_MS = 1500;
    var timer = null;
    // 导入弹窗打开期间置位：弹窗内已有进度，浮标让位（同屏两份进度像两个任务，
    // 且省掉一条重复轮询）。由 import.js 的 openDialog/closeDialog 驱动。
    var dialogOpen = false;

    function getTask() { try { return sessionStorage.getItem(KEY); } catch (e) { return null; } }
    function setTask(id) { try { sessionStorage.setItem(KEY, id); } catch (e) {} }
    function clearTask() { try { sessionStorage.removeItem(KEY); } catch (e) {} }

    function node() { return document.getElementById('import-floater'); }

    function remove() {
        var n = node();
        if (n) n.remove();
    }

    // 服务端 message 可能夹带异常文本，一律转义后再插入（防 XSS）
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function render(html) {
        var n = node();
        if (!n) {
            n = document.createElement('div');
            n.id = 'import-floater';
            n.className = 'import-floater';
            document.body.appendChild(n);
        }
        n.innerHTML = html;
    }

    function toast(msg) { if (window.showSearchToast) window.showSearchToast(msg, 4000); }

    function stop() { if (timer) { clearInterval(timer); timer = null; } }

    function showProgress(p) {
        if (dialogOpen) { remove(); return; }
        var pct = Number(p && p.progress);
        if (!isFinite(pct)) pct = 0;
        pct = Math.max(0, Math.min(100, Math.round(pct)));
        // 做成**可点**：它是用户此刻唯一的进度线索，必须给出去处（打开导入弹窗看详情）。
        // 转圈动画是为了回答"到底还在跑吗"——OCR 阶段的百分比会长时间停在 20 不动
        // （OCR 客户端只在排队重试时回消息，从不回百分比），静止的浮标读起来像卡死。
        // 用原生 onclick 而非框架绑定：本节点由 JS 动态创建，没有框架上下文。
        render('<button type="button" class="import-floater__btn"'
            + ' onclick="ImportTracker.openImportUI()">'
            + '<span class="import-floater__title"><span class="import-floater__spin"></span>'
            + '📥 导入中 ' + pct + '%</span>'
            + '<span class="import-floater__msg">' + esc(p && p.message) + '</span>'
            + '</button>');
    }

    // 待审查：给出口，且**保留** task_id —— 浮标本身就是那个入口
    function showReview() {
        var id = getTask();
        if (!id) { remove(); return; }
        render('<a class="import-floater__link" href="/import/review/'
            + encodeURIComponent(id) + '">'
            + '<span class="import-floater__title">📝 待审查</span>'
            + '<span class="import-floater__msg">点击继续审查</span></a>');
    }

    function apply(p) {
        var st = (p && p.status) || 'unknown';
        if (st === 'done') {
            stop(); clearTask(); remove(); toast('导入已完成');
        } else if (st === 'error') {
            stop(); clearTask(); remove();
            toast('导入失败：' + ((p && p.message) || ''));
        } else if (st === 'unknown') {
            // 服务重启后 progress_store 清空 —— 属**正常**路径，自清而不是永久轮询
            stop(); clearTask(); remove();
        } else if (st === 'review_needed') {
            stop();                       // 不再轮询，但保留 task_id
            showReview();
        } else {
            showProgress(p);              // uploading / processing
        }
    }

    async function poll() {
        var id = getTask();
        if (!id) { stop(); remove(); return; }
        try {
            var r = await fetch('/import/progress/' + encodeURIComponent(id) + '/json');
            if (!r.ok) return;            // 瞬时失败忽略，下轮继续
            apply(await r.json());
        } catch (e) { /* 轮询瞬时失败忽略，下轮继续 */ }
    }

    function start() {
        if (timer) return;
        timer = setInterval(poll, POLL_MS);
        poll();
    }

    window.ImportTracker = {
        // 导入任务启动时调用（import.js 从上传响应的 X-Import-Task-Id 头取到）
        start: function (id) { if (!id) return; setTask(id); start(); },

        // 打开导入弹窗看详情。带导入弹窗的页面（左栏 tree_panel 在）直接点它；
        // 审查页隐藏了左栏（hide_tree）没有这个按钮 → 跳回首页带参数自动打开。
        openImportUI: function () {
            var trigger = document.querySelector('[data-import-dialog-trigger]');
            if (trigger) { trigger.click(); return; }
            location.href = '/?open_import=1';
        },

        // 把进行中任务的进度片段恢复进导入弹窗。
        // 为什么需要：整页跳转会销毁弹窗 DOM 里原有的 #import-status（HTMX 轮询随之
        // 消失）。不恢复的话，用户「中途退出 → 切页 → 再点导入」只看到一个空表单，
        // 于是以为任务丢了——而任务其实还在服务端跑着。
        resumeInto: function (el) {
            var id = getTask();
            if (!el || !id) return;
            if (el.querySelector('#import-status')) {   // 已有进度节点（同页关而复开）
                if (window.htmx) window.htmx.process(el);
                return;
            }
            fetch('/import/progress/' + encodeURIComponent(id))
                .then(function (r) { return r.ok ? r.text() : ''; })
                .then(function (html) {
                    if (!html) return;
                    el.innerHTML = html;
                    // 片段自带 hx-get/hx-trigger，需 htmx 处理新节点才会开始轮询
                    if (window.htmx) window.htmx.process(el);
                })
                .catch(function () { /* 拉取失败就留空，浮标仍在提示 */ });
        },
        stop: function () { stop(); remove(); },
        setDialogOpen: function (open) {
            dialogOpen = !!open;
            if (dialogOpen) { stop(); remove(); }
            else if (getTask()) { start(); }
        },
        get current() { return getTask(); },
    };

    // 页面加载恢复：sessionStorage 有任务（如用户从导入页切来）→ 继续跟踪
    if (getTask()) start();
})();
