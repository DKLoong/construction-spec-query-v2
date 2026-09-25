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
        // 挂到 body：fixed 定位基准为视口，不随 .center-panel-v2 滚动/htmx 刷新被冲掉
        document.body.appendChild(el);
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
        get statusCurrent() {
            return this.$store.searchState.statusCurrent;
        },
        set statusCurrent(v) {
            this.$store.searchState.statusCurrent = v;
        },
        get statusRevising() {
            return this.$store.searchState.statusRevising;
        },
        set statusRevising(v) {
            this.$store.searchState.statusRevising = v;
        },

        // 勾选/取消「启用 CE 精排」→ 弹提示 + 立即重搜（热切换，结果实时按新开关排序）
        onCeChange() {
            // ⚠️ QA 页**不弹**这个提示：文案说的是"搜索结果"，而 QA 页勾选 CE 只更新状态 + 同步 URL、
            // 不发起检索（D8）⇒ 弹出来会误导用户（用户实测反馈）。
            // 故先判视图再决定是否提示：守卫 + 同步 + return 必须在 toast **之前**。
            if (typeof isQaView === 'function' && isQaView()) { syncQaUrl(); return; }
            if (this.ceRerank) {
                showSearchToast('CE精排已开启，请耐心等待搜索结果');
            }
            this.search();
        },

        // 勾选/取消「仅现行」「修订中」→ 全不勾选时顶部轻提示（复用 showSearchToast）+ 立即重搜
        onStatusChange() {
            // 同 onCeChange：QA 页不弹（文案"当前展示结果"指的是检索结果，与 QA 的下一轮筛选无关）
            if (typeof isQaView === 'function' && isQaView()) { syncQaUrl(); return; }
            if (!this.statusCurrent && !this.statusRevising) {
                showSearchToast('注意：当前展示结果未过滤非现行规范', 4000);
            }
            this.search();
        },

        async search() {
            // 用户发起一次新查询（非翻页）：重置"本次查询已提示过CE"标记，
            // 使后续对新结果集翻页过半/超3页时能再次提示（Bug1 根因：标记只随整页刷新重置）
            resetCeSuggest();
            this.loading = true;
            const params = new URLSearchParams();
            const kw = (this.$store.searchState.keyword || '').trim();
            if (kw) params.append('keyword', kw);
            // 携带当前分类筛选，避免「先选分类再搜关键词」丢失筛选状态
            // 维度值为数组（同维多选）：逐个 append，同名参数后端聚合为列表（OR 语义）
            for (const [k, v] of Object.entries(this.$store.searchState.filters)) {
                if (Array.isArray(v)) v.forEach(x => params.append(k, x));
                else params.append(k, v);
            }
            // 勾选「包含前言·条文说明」时放行打标非条文
            if (this.includeNonClause) params.append('include_non_clause', '1');
            // CE 精排热切换：开启时携带 ce_rerank
            if (this.ceRerank) params.append('ce_rerank', '1');
            // 状态过滤：buildStatusFilter() 全不勾返回 null → 不过滤（旧行为兼容）
            const sf = this.$store.searchState.buildStatusFilter();
            if (sf) params.append('status_filter', sf);
            // 无关键词无筛选（如清空搜索框后回车）→ 显式请求全部条文
            if (!kw && Object.keys(this.$store.searchState.filters).length === 0) {
                params.append('all', '1');
            }
            htmx.ajax('GET', `/search?${params.toString()}`, {
                target: '.center-panel-v2',
                swap: 'innerHTML'
            });
            // 若刚才在 QA 页，换入结果后把地址栏推回检索页，避免"内容已是检索页、URL 还是 /qa"
            // （项目没有「整页导航到检索结果」这条路径：GET /search 只返回片段，GET / 不收关键词，
            //  故 htmx 换入是唯一形态；换入后 #qa-root 随 .center-panel-v2 一起消失，
            //  之后 isQaView() 自然为 false，左栏点击回到检索页语义）
            if (typeof isQaView === 'function' && isQaView()) history.pushState(null, '', '/');
            this.loading = false;
        },
    }));
});

// 翻页过半建议：兜底提前 + 每个查询只提示一次（同一结果集内翻页不重复打扰）
// 触发阈值 = min(floor(total_pages/2), _SUGGEST_PAGE_CAP)——大页数库（万级条文）
// 下过半太晚，用绝对页数上限提前提示；_suggestShown 标记同一查询内只弹一次，
// 用户发起新查询（search()/dispatchSearch()）时经 resetCeSuggest() 复位。
const _SUGGEST_PAGE_CAP = 3;
let _suggestShown = false;

// 复位"本次查询已提示CE"标记：供 search.js 与 tree.js 的用户新查询入口调用
function resetCeSuggest() {
    _suggestShown = false;
}

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
document.body.addEventListener('htmx:afterSettle', (evt) => {
    hideRadar();
    // 检索结果就绪即回顶。htmx 的 afterSettle 只在「swap 目标」上触发：#search-results
    // 的 hx-on 回顶仅覆盖翻页(目标=#search-results)，而搜索框换词/分类树筛选走
    // search()/dispatchSearch()，swap 目标是 .center-panel-v2，事件不下传子节点 → 需在此
    // 按请求路径统一回顶，保证每次更换关键词/筛选后滚动条回到顶部。
    try {
        const path = (evt.detail && evt.detail.requestConfig && evt.detail.requestConfig.path) || '';
        if (path.startsWith('/search')) {
            const panel = document.querySelector('.center-panel-v2');
            if (panel) panel.scrollTop = 0;
        }
    } catch (e) { /* 无面板/未就绪时忽略 */ }
    // 翻页建议：未开 CE 且翻页超过「过半 与 最多3页 取小值」→ 提示开启 CE（展示 4s）
    try {
        const res = document.querySelector('#search-results');
        if (!res) return;
        const page = parseInt(res.dataset.page || '1', 10);
        const totalPages = parseInt(res.dataset.totalPages || '0', 10);
        const ceOn = res.dataset.ceRerank === '1';
        const triggerPage = Math.min(Math.floor(totalPages / 2), _SUGGEST_PAGE_CAP);
        if (!_suggestShown && !ceOn && totalPages > 1 && page > triggerPage) {
            _suggestShown = true;  // 每会话仅提示一次，避免后续翻页反复打扰
            showSearchToast('对搜索结果不满意？请尝试在左侧开启CE精排', 4000);
        }
    } catch (e) { /* 结果区未就绪时忽略 */ }
});
