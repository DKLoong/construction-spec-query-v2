// 条文详情弹窗组件
document.addEventListener('alpine:init', () => {
    Alpine.data('clauseModal', () => ({
        open: false,
        loading: false,

        init() {
            // 监听来自搜索结果项的自定义事件
            window.addEventListener('view-clause', (e) => {
                this.viewClause(e.detail.id);
            });
        },

        viewClause(id) {
            this.open = true;
            this.loading = true;
            // 通过 HTMX 加载条文详情到弹窗内
            htmx.ajax('GET', `/clause/${id}`, {
                target: '#clause-modal-content',
                swap: 'innerHTML'
            });
            // 内容加载完成后取消 loading
            const self = this;
            const content = document.getElementById('clause-modal-content');
            content.addEventListener('htmx:afterSettle', () => {
                self.loading = false;
            }, { once: true });
        },

        close() {
            this.open = false;
            this.loading = false;
            // 清空弹窗内容
            const content = document.getElementById('clause-modal-content');
            if (content) content.innerHTML = '';
        }
    }));
});
