// 搜索组件：关键词与分类树筛选共享 searchState store
document.addEventListener('alpine:init', () => {
    Alpine.data('searchBox', () => ({
        loading: false,

        // keyword 代理到共享 store（搜索框输入与分类树触发读取同一状态）
        get keyword() {
            return this.$store.searchState.keyword;
        },
        set keyword(v) {
            this.$store.searchState.keyword = v;
        },

        async search() {
            this.loading = true;
            const params = new URLSearchParams();
            const kw = (this.$store.searchState.keyword || '').trim();
            if (kw) params.append('keyword', kw);
            // 携带当前分类筛选，避免「先选分类再搜关键词」丢失筛选状态
            for (const [k, v] of Object.entries(this.$store.searchState.filters)) {
                params.append(k, v);
            }
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
