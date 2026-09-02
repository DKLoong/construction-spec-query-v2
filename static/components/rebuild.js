// 重建向量索引后台任务跨页追踪器（base.html 全局加载，任意页面存活）
//
// 背景：向量重建是长耗时后台任务，用户可能切到其他页面继续操作。task_id 存
// sessionStorage——切页/返回均不丢；本脚本在每页加载时若发现有进行中任务即恢复轮询，
// 完成/失败时全局弹 4s 轻提示；维护页通过 rebuild-progress / rebuild-finished 事件
// 渲染进度环（环元素仅在维护页存在，本脚本不做任何 DOM 结构假设）。
(function () {
    var KEY = 'rebuildTaskId';
    var timer = null;

    function getTask() { try { return sessionStorage.getItem(KEY); } catch (e) { return null; } }
    function setTask(id) { try { sessionStorage.setItem(KEY, id); } catch (e) {} }
    function clearTask() { try { sessionStorage.removeItem(KEY); } catch (e) {} }

    function toast(msg) { if (window.showSearchToast) showSearchToast(msg, 4000); }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }

    function start() {
        if (timer) return;
        timer = setInterval(poll, 1500);
        poll();
    }

    async function poll() {
        var id = getTask();
        if (!id) { stop(); return; }
        try {
            var r = await fetch('/maintenance/rebuild-progress/' + id);
            var p = await r.json();
            // 广播进度（维护页进度环消费；其他页无环元素则忽略）
            window.dispatchEvent(new CustomEvent('rebuild-progress', { detail: p }));
            if (p.status === 'done' || p.status === 'error') {
                stop();
                clearTask();
                if (p.status === 'done') toast('向量索引重建已完成');
                else toast('向量索引重建失败');
                window.dispatchEvent(new CustomEvent('rebuild-finished', { detail: p }));
            }
        } catch (e) { /* 轮询瞬时失败忽略，下轮继续 */ }
    }

    window.RebuildTracker = {
        start: function (id) { setTask(id); start(); },
        stop: stop,
        get active() { return !!getTask(); },
        get current() { return getTask(); },
    };

    // 页面加载恢复：sessionStorage 有进行中任务（如用户从维护页切来）→ 继续轮询
    if (getTask()) start();
})();
