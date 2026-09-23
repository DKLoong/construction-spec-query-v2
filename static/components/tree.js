// 分类树组件 + 全局搜索状态
// searchState 是搜索框 / 分类树 / AI 问答共享的单一事实源：
//   { keyword, filters } — keyword 为搜索词，filters 为分类维度选中项（dim1~dim6）

// 当前是否处于 QA 视图：以 DOM 存在性判断。**全站唯一实现**（qa.js / search.js 复用），
// 不要在别处另写判据——两处判据将来会漂移。
// 不用 Alpine 生命周期钩子——htmx/DOM 替换场景下 destroy() 是否触发未被官方文档化，
// 用 DOM 判据零风险且同样准确（项目其它地方也按 DOM 状态判断）。
function isQaView() {
    return !!document.getElementById('qa-root');
}

document.addEventListener('alpine:init', () => {
    Alpine.store('searchState', {
        keyword: '',
        filters: {},
        includeNonClause: false,
        ceRerank: false,
        statusCurrent: true,     // 「仅现行」默认勾选（D3）
        statusRevising: false,
        // 状态过滤组合：仅现行→'现行'；仅现行+修订中→'现行,修订中'；仅修订中→'修订中'；全不勾→null（不过滤+轻提示）
        // 注意：store 方法内 this 即 searchState store 本身，不能使用 this.$store（$store magic 仅在组件/DOM 表达式上下文生效）
        buildStatusFilter() {
            const c = this.statusCurrent;
            const r = this.statusRevising;
            if (c && r) return '现行,修订中';
            if (c) return '现行';
            if (r) return '修订中';
            return null;
        },
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
            // 同维多选：每个维度存数组，点击切换（选中→移除，未选→追加）；清空则删除该键
            const next = { ...this.$store.searchState.filters };
            const arr = Array.isArray(next[dimension]) ? next[dimension].slice() : [];
            const idx = arr.indexOf(value);
            if (idx >= 0) arr.splice(idx, 1); else arr.push(value);
            if (arr.length) next[dimension] = arr; else delete next[dimension];
            this.$store.searchState.filters = next;
            // QA 页：筛选只更新共享状态（影响下一轮问答检索）+ 同步 URL，
            // 不发起检索——否则点分类树会把用户弹回检索页（设计文档 D8）
            if (isQaView()) {
                if (typeof syncQaUrl === 'function') syncQaUrl();
                return;
            }
            this.dispatchSearch();
        },

        async dispatchSearch() {
            // 分类树操作 = 用户新查询入口：复位"本次查询已提示CE"标记（与搜索框 search() 对齐）
            if (typeof resetCeSuggest === 'function') resetCeSuggest();
            try {
                const params = new URLSearchParams();
                // 从共享 store 读取筛选维度 + 搜索关键词（与搜索框保持一致）
                // 维度值为数组（同维多选）：逐个 append，同名参数后端聚合为列表（OR 语义）
                for (const [k, v] of Object.entries(this.$store.searchState.filters)) {
                    if (Array.isArray(v)) v.forEach(x => params.append(k, x));
                    else params.append(k, v);
                }
                const kw = (this.$store.searchState.keyword || '').trim();
                if (kw) {
                    params.append('keyword', kw);
                }
                // 与搜索框复选框状态保持一致：分类树触发搜索也携带 include_non_clause
                if (this.$store.searchState.includeNonClause) {
                    params.append('include_non_clause', '1');
                }
                // CE 精排热切换：开启时携带 ce_rerank
                if (this.$store.searchState.ceRerank) {
                    params.append('ce_rerank', '1');
                }
                // 状态过滤：buildStatusFilter() 全不勾返回 null → 不过滤（旧行为兼容）
                const sf = this.$store.searchState.buildStatusFilter();
                if (sf) {
                    params.append('status_filter', sf);
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
