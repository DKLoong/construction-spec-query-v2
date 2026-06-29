// 搜索组件
document.addEventListener('alpine:init', () => {
    Alpine.data('searchBox', () => ({
        keyword: '',
        loading: false,

        async search() {
            this.loading = true;
            const params = new URLSearchParams();
            if (this.keyword) params.append('keyword', this.keyword);
            // 使用 htmx.ajax() 而非 fetch()，确保 HTMX 正确初始化新元素上的 hx-* 属性
            const panel = document.querySelector('.center-panel-v2');
            // 内容加载完成后滚动到顶部
            panel.addEventListener('htmx:afterSettle', function scrollTop() {
                panel.scrollTop = 0;
                panel.removeEventListener('htmx:afterSettle', scrollTop);
            });
            htmx.ajax('GET', `/search?${params.toString()}`, {
                target: '.center-panel-v2',
                swap: 'innerHTML'
            });
            this.loading = false;
        },
    }));
});
