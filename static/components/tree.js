// 分类树组件
document.addEventListener('alpine:init', () => {
    Alpine.data('treeView', () => ({
        dimensions: [],
        searchKeyword: '',
        activeFilters: {},
        loading: false,

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
            if (this.activeFilters[dimension] === value) {
                delete this.activeFilters[dimension];
            } else {
                this.activeFilters[dimension] = value;
            }
            this.dispatchSearch();
        },

        async dispatchSearch() {
            const params = new URLSearchParams();
            for (const [k, v] of Object.entries(this.activeFilters)) {
                params.append(k, v);
            }
            // 从搜索框读取关键词，使维度筛选也能带上搜索词
            const keywordInput = this.$el.querySelector('input[type="search"]');
            const kw = keywordInput ? keywordInput.value.trim() : '';
            if (kw) {
                params.append('keyword', kw);
            }
            // 使用 htmx.ajax() 而非 fetch()，确保 HTMX 正确初始化新元素上的 hx-* 属性
            htmx.ajax('GET', `/search?${params.toString()}`, {
                target: '.center-panel',
                swap: 'innerHTML'
            });
        },
    }));
});
