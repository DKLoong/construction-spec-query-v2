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
            htmx.ajax('GET', `/search?${params.toString()}`, {
                target: '.center-panel',
                swap: 'innerHTML'
            });
            this.loading = false;
        },
    }));
});
