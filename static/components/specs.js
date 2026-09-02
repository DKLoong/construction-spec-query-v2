// 规范页状态行内切换：监听 specs_table 下拉触发的 spec-status-change 事件，
// 以 PUT /specs/{id}/status 提交新状态（值域校验由后端兜底），成功后轻提示。
// 注意：specs_table 用 window.dispatchEvent 派发事件，监听必须用 window.addEventListener
// （document 监听收不到 window 上 dispatch 的事件），与 qa.js/clause-modal.js 的 view-clause 惯例一致。
window.addEventListener('spec-status-change', (e) => {
    const { id, status } = e.detail;
    fetch(`/specs/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ status }),
    }).then(r => {
        if (r.ok) {
            // 轻量成功提示
            const el = document.querySelector(`.spec-status-select[data-id="${id}"]`);
            if (el) el.style.outline = '2px solid var(--pico-primary)';
            setTimeout(() => { if (el) el.style.outline = ''; }, 800);
        } else {
            alert('状态更新失败');
        }
    }).catch(err => {
        console.error('状态更新请求失败:', err);
        alert('状态更新失败，请检查网络');
    });
});
