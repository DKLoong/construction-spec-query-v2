// 搜索组件：关键词与分类树筛选共享 searchState store
// 轻提示 / 雷达动画为模块级函数，供 searchBox、dispatchSearch、翻页建议复用

function showSearchToast(msg, duration = 2000) {
    let el = document.getElementById('search-toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'search-toast';
        el.className = 'search-toast';
        // 挂到 body：不随 .center-panel-v2 的 htmx innerHTML 刷新被冲掉
        document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove('show'), duration);
}

function showRadar() {
    let el = document.getElementById('radar-overlay');
    if (!el) {
        el = document.createElement('div');
        el.id = 'radar-overlay';
        el.className = 'radar-overlay';
        el.innerHTML = '<div class="radar-box">'
            + '<div class="radar-circle"></div>'
            + '<div class="radar-sweep"></div>'
            + '<div class="radar-text">CE 精排中，请稍候…</div></div>';
        const center = document.querySelector('.center-panel-v2');
        if (center) center.appendChild(el);
    }
    el.classList.add('show');
}

function hideRadar() {
    const el = document.getElementById('radar-overlay');
    if (el) el.classList.remove('show');
}

document.addEventListener('alpine:init', () => {
    Alpine.data('searchBox', () => ({
        loading: false,

        // keyword / includeNonClause / ceRerank 都代理到共享 store
        get keyword() {
            return this.$store.searchState.keyword;
        },
        set keyword(v) {
            this.$store.searchState.keyword = v;
        },
        get includeNonClause() {
            return this.$store.searchState.includeNonClause;
        },
        set includeNonClause(v) {
            this.$store.searchState.includeNonClause = v;
        },
        get ceRerank() {
            return this.$store.searchState.ceRerank;
        },
        set ceRerank(v) {
            this.$store.searchState.ceRerank = v;
        },

        // 勾选/取消「启用 CE 精排」→ 弹提示 + 立即重搜（热切换，结果实时按新开关排序）
        onCeChange() {
            if (this.ceRerank) {
                showSearchToast('CE精排已开启，请耐心等待搜索结果');
            }
            this.search();
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
            // CE 精排热切换：开启时携带 ce_rerank
            if (this.ceRerank) params.append('ce_rerank', '1');
            // 无关键词无筛选（如清空搜索框后回车）→ 显式请求全部条文
            if (!kw && Object.keys(this.$store.searchState.filters).length === 0) {
                params.append('all', '1');
            }
            htmx.ajax('GET', `/search?${params.toString()}`, {
                target: '.center-panel-v2',
                swap: 'innerHTML'
            });
            this.loading = false;
        },
    }));
});

// 全局一次注册（不随每次搜索累积监听）：雷达动画显示/隐藏 + 翻页建议
document.body.addEventListener('htmx:beforeRequest', (evt) => {
    try {
        // 仅搜索请求触发雷达（排除条文详情等其它 htmx 请求）；CE 开启时显示
        const path = (evt.detail && evt.detail.requestConfig && evt.detail.requestConfig.path) || '';
        if (path.startsWith('/search') && Alpine.store('searchState').ceRerank) {
            showRadar();
        }
    } catch (e) { /* Alpine 未初始化时忽略 */ }
});
document.body.addEventListener('htmx:afterSettle', () => {
    hideRadar();
    // 翻页建议：未开 CE 且翻页超过总页数一半（向下取整）→ 提示开启 CE（展示 4s）
    try {
        const res = document.querySelector('#search-results');
        if (!res) return;
        const page = parseInt(res.dataset.page || '1', 10);
        const totalPages = parseInt(res.dataset.totalPages || '0', 10);
        const ceOn = res.dataset.ceRerank === '1';
        if (!ceOn && totalPages > 1 && page > Math.floor(totalPages / 2)) {
            showSearchToast('对搜索结果不满意？请尝试在左侧开启CE精排', 4000);
        }
    } catch (e) { /* 结果区未就绪时忽略 */ }
});
