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
            const params = new URLSearchParams();
            // 从共享 store 读取筛选维度 + 搜索关键词（与搜索框保持一致）
            for (const [k, v] of Object.entries(this.$store.searchState.filters)) {
                params.append(k, v);
            }
            const kw = (this.$store.searchState.keyword || '').trim();
            if (kw) {
                params.append('keyword', kw);
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
        },
    }));
});
