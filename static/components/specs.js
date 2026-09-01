// 规范页状态行内切换：监听 specs_table 下拉触发的 spec-status-change 事件，
// 以 PUT /specs/{id}/status 提交新状态（值域校验由后端兜底），成功后轻提示。
document.addEventListener('spec-status-change', (e) => {
    const { id, status } = e.detail;
    const resp = fetch(`/specs/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ status }),
    });
    resp.then(r => {
        if (r.ok) {
            // 轻量成功提示
            const el = document.querySelector(`.spec-status-select[data-id="${id}"]`);
            if (el) el.style.outline = '2px solid var(--pico-primary)';
            setTimeout(() => { if (el) el.style.outline = ''; }, 800);
        } else {
            alert('状态更新失败');
        }
    });
});
