// 条文详情弹窗组件
document.addEventListener('alpine:init', () => {
    Alpine.data('clauseModal', () => ({
        loading: false,

        init() {
            // 监听来自搜索结果项的自定义事件
            window.addEventListener('view-clause', (e) => {
                this.viewClause(e.detail.id);
            });
            // 页面加载时隐藏弹窗（style.display 兜底，避免 x-show 与 x-cloak 交互的 Alpine 懒加载问题）
            const el = this.$el;
            if (el) el.style.display = 'none';
        },

        viewClause(id) {
            const el = this.$el;
            if (!el) return;
            this.loading = true;
            // 直接操作 style.display 绕过 Alpine x-show（后者在特定场景与 x-cloak 配合时可见性切换失效）
            el.style.display = 'flex';
            // 通过 HTMX 加载条文详情到弹窗内
            htmx.ajax('GET', `/clause/${id}`, {
                target: '#clause-modal-content',
                swap: 'innerHTML'
            });
            // 内容加载完成后取消 loading
            const content = document.getElementById('clause-modal-content');
            const self = this;
            content.addEventListener('htmx:afterSettle', () => {
                self.loading = false;
            }, { once: true });
        },

        close() {
            const el = this.$el;
            if (el) el.style.display = 'none';
            this.loading = false;
            // 清空弹窗内容
            const content = document.getElementById('clause-modal-content');
            if (content) content.innerHTML = '';
        }
    }));
});
