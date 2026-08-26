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

        // 复选框「包含前言·条文说明」代理到共享 store（与分类树/搜索框状态一致）
        get includeNonClause() {
            return this.$store.searchState.includeNonClause;
        },
        set includeNonClause(v) {
            this.$store.searchState.includeNonClause = v;
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
            // 勾选「包含前言·条文说明」时放行打标非条文
            if (this.includeNonClause) params.append('include_non_clause', '1');
            // 无关键词无筛选（如清空搜索框后回车）→ 显式请求全部条文
            if (!kw && Object.keys(this.$store.searchState.filters).length === 0) {
                params.append('all', '1');
            }
            // 滚动到顶部由结果页 #search-results 的 hx-on::after-settle 处理，
            // 不在每次请求前累积 htmx:afterSettle 监听器
            htmx.ajax('GET', `/search?${params.toString()}`, {
                target: '.center-panel-v2',
                swap: 'innerHTML'
            });
            this.loading = false;
        },
    }));
});
