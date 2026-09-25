// ── 条文列表内容列预览渲染 ────────────────────────────────────────
// 统一走 mdRender（marked + DOMPurify + KaTeX + 图片相对路径改写）。
//
// 用 htmx:afterSettle 钩子而非 partial 内联脚本：行级 swap（条文分类的保存/取消）
// 换入的行不会重跑「表格级」脚本，只有钩子覆盖得到，否则回填行的内容列会退化成
// 纯文本、与初次渲染不一致。data-rendered 防重复渲染。
function renderClausePreviews() {
    document.querySelectorAll('.clause-preview-md:not([data-rendered])').forEach(function (el) {
        el.setAttribute('data-rendered', '1');
        let md = '';
        try { md = JSON.parse(el.dataset.md || '""'); } catch (e) { md = ''; }
        window.mdRender.renderInto(el, md, '/specs/' + el.dataset.specId + '/');
    });
}
document.body.addEventListener('htmx:afterSettle', renderClausePreviews);
renderClausePreviews();   // 初次（非 htmx 路径的兜底）

// ── 规范状态行内切换（带确认，防误触） ──────────────────────────────
// 下拉框的 onchange 直接调本函数。规范状态影响检索默认过滤（「仅现行」下废止规范
// 会从结果里消失），误改一次的代价远大于一次确认。
//
// 取消 = 把下拉框拨回 data-prev（模板下发的原值）并**不发请求**；
// 失败同样回滚，避免界面显示的值与实际状态不一致（比报错更危险）。
window.specStatusChange = function (sel) {
    const id = sel.dataset.id;
    const next = sel.value;
    const prev = sel.dataset.prev;

    if (next === prev) return;   // 选了同一项：不弹框、不发请求

    if (!window.confirm(`规范状态已修改为「${next}」，是否应用？`)) {
        sel.value = prev;        // 取消：保持修改前状态
        return;
    }

    const rollback = function () { sel.value = prev; };
    fetch(`/specs/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ status: next }),
    }).then(r => {
        if (!r.ok) { alert('状态更新失败'); rollback(); return; }
        sel.dataset.prev = next;   // 成功：原值前移，后续切换以新值为基准
        sel.style.outline = '2px solid var(--pico-primary)';
        setTimeout(() => { sel.style.outline = ''; }, 800);
    }).catch(err => {
        console.error('状态更新请求失败:', err);
        alert('状态更新失败，请检查网络');
        rollback();
    });
};
