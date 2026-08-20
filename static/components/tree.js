// 分类树组件 + 全局搜索状态
// searchState 是搜索框 / 分类树 / AI 问答共享的单一事实源：
//   { keyword, filters } — keyword 为搜索词，filters 为分类维度选中项（dim1~dim6）
document.addEventListener('alpine:init', () => {
    Alpine.store('searchState', {
        keyword: '',
        filters: {},
    });

    Alpine.data('treeView', () => ({
        dimensions: [],
        loading: false,

        // 分类树选中状态代理到共享 store（替换整对象以触发 Alpine 响应式）
        get activeFilters() {
            return this.$store.searchState.filters;
        },

        async init() {
            await this.loadTree();
        },

        async loadTree() {
            try {
                const resp = await fetch('/tree/all');
                this.dimensions = await resp.json();
            } catch (e) {
                console.error('加载分类树失败:', e);
            }
        },

        toggleNode(node) {
            node.expanded = !node.expanded;
        },

        selectFilter(dimension, value) {
            const next = { ...this.$store.searchState.filters };
            if (next[dimension] === value) {
                delete next[dimension];
            } else {
                next[dimension] = value;
            }
            this.$store.searchState.filters = next;
            this.dispatchSearch();
        },

        async dispatchSearch() {
            try {
                const params = new URLSearchParams();
                // 从共享 store 读取筛选维度 + 搜索关键词（与搜索框保持一致）
                for (const [k, v] of Object.entries(this.$store.searchState.filters)) {
                    params.append(k, v);
                }
                const kw = (this.$store.searchState.keyword || '').trim();
                if (kw) {
                    params.append('keyword', kw);
                }
                // 用户主动操作但无关键词无筛选（如取消所有筛选）→ 显式请求全部条文
                if (!kw && Object.keys(this.$store.searchState.filters).length === 0) {
                    params.append('all', '1');
                }
                // 滚动到顶部由结果页 #search-results 的 hx-on::after-settle 处理，
                // 不在每次请求前累积 htmx:afterSettle 监听器（避免快速操作时竞态/跳顶）
                htmx.ajax('GET', `/search?${params.toString()}`, {
                    target: '.center-panel-v2',
                    swap: 'innerHTML'
                });
            } catch (e) {
                console.error('[dispatchSearch] 异常:', e);
            }
        },
    }));
});
