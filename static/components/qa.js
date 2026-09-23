// AI 问答组件

// ── QA 页 URL 筛选携带（D4）──
// 整页导航会重置 Alpine store，筛选靠 URL 携带才不丢；顺带得到可分享链接。

// 维度参数名。**必须与后端 app/models.py 的 QA_DIM_FIELDS 保持一致**
// （前端无法跨语言复用该常量，只能镜像；后端有 test_qa_dim_fields_matches_request_model
//  盯着常量与 QaRequest 的一致性，此处靠代码评审与 URL 回填探针发现漂移。
//  T1 的 t1_filters_carry_into_qa_via_url 会在维度名漂移时失败。）
const QA_DIM_KEYS = ['dim1_hierarchy', 'dim1_industry', 'dim1_nature',
                     'dim2_stage', 'dim3_usage', 'dim4_specialty',
                     'dim5_location', 'dim6_material'];

// 由共享 store 生成 QA 页 URL；无筛选时退化为裸 /qa
function buildQaUrl() {
    const ss = window.Alpine && window.Alpine.store && Alpine.store('searchState');
    if (!ss) return '/qa';
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(ss.filters || {})) {
        if (Array.isArray(v)) v.forEach(x => p.append(k, x));
        else p.append(k, v);
    }
    const sf = ss.buildStatusFilter();
    if (sf) p.append('status_filter', sf);
    if (ss.includeNonClause) p.append('include_non_clause', '1');
    const qs = p.toString();
    return qs ? `/qa?${qs}` : '/qa';
}

// 在 QA 页内同步 URL（replaceState：不污染历史栈，避免每次勾选都多一条记录）
function syncQaUrl() {
    if (typeof isQaView === 'function' && isQaView()) {
        history.replaceState(null, '', buildQaUrl());
    }
}

document.addEventListener('alpine:init', () => {
    Alpine.data('qaView', () => ({
        messages: [],
        input: '',
        loading: false,
        mode: 'rag',            // rag 综合问答（默认） / verbatim 原文摘抄
        // 会话栏折叠态：T1 骨架的「◂ 会话」按钮已引用它；完整会话栏由 T4 填充
        sessionsCollapsed: false,

        // 本轮实际生效的筛选（D5）：回复中改筛选只影响下一轮，靠它与当前选中比对出提示。
        // **本 Task 就要有**：filtersChanged() 第一行读它，留到 T4 才定义会让
        // 「将在下一轮生效」提示在整个 T3/T4 阶段恒不出现（静默失效）。
        effectiveFiltersText: '',

        // 状态过滤改为读左栏共享 store（统一操作逻辑）：
        // QA 面板内不再有独立复选框，「仅现行 / 修订中」一律由左栏控制
        get statusFilter() {
            return this.$store.searchState.buildStatusFilter();
        },

        async init() {
            this.seedFiltersFromUrl();   // T1：URL → store 回填
            // await this.loadSessions();  ← T4 引入 loadSessions() 后在此启用
            this.scrollToBottom();
        },

        // 从 URL 回填共享筛选状态。维度用 getAll（同维多选）。
        seedFiltersFromUrl() {
            const ss = this.$store.searchState;
            const p = new URLSearchParams(window.location.search);
            const filters = {};
            for (const k of QA_DIM_KEYS) {
                const vals = p.getAll(k).filter(Boolean);
                if (vals.length) filters[k] = vals;
            }
            ss.filters = filters;
            const sf = p.get('status_filter');
            if (sf !== null) {
                // 与 buildStatusFilter() 的取值域对称：'现行' / '现行,修订中' / '修订中'
                ss.statusCurrent = sf.includes('现行');
                ss.statusRevising = sf.includes('修订中');
            }
            ss.includeNonClause = p.get('include_non_clause') === '1';
        },

        toggleMode() {
            this.mode = (this.mode === 'rag') ? 'verbatim' : 'rag';
        },

        async send() {
            const q = this.input.trim();
            if (!q || this.loading) return;
            this.messages.push({ role: 'user', content: q });
            this.input = '';
            this.loading = true;
            this.scrollToBottom();

            try {
                // 携带当前筛选（分类维度 + 状态 + 前言放行），收窄检索范围提升精确度。
                // 统一由 buildRequestBody() 组合（读共享 store），QA 面板内不再自持状态。
                const resp = await fetch('/qa/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.buildRequestBody(q)),
                });
                const data = await resp.json();
                // 写入本轮生效筛选：否则 .qa-filters-pending（「将在下一轮生效」）恒不出现
                // （后端 /qa/ask 的 JSON 响应已带 effective_filters）
                this.effectiveFiltersText = this.describeFilters(data.effective_filters);
                const answer = data.answer || '(AI 未返回回答)';
                // role 值域是后端的 {user, assistant}（qa_messages.role），**不要**在本地另造 'bot' 别名：
                // 新模板的助手分支判的就是 `msg.role === 'assistant'`，写成 'bot' 会让每条回答都渲染成空气泡。
                // 同时必须给 `html`（模板用 x-html="msg.html" 渲染回答）。
                this.messages.push({
                    role: 'assistant',
                    content: answer,
                    html: this.renderMarkdown(answer, data.sources || []),
                    sources: data.sources || [],
                    confusable: data.confusable_hits || [],
                });
            } catch (e) {
                console.error('[qa] 提问失败:', e);
                // 失败提示同样要给 html（固定字面量、无用户内容，无需转义）
                this.messages.push({ role: 'assistant', content: '请求失败，请稍后重试',
                                     html: '请求失败，请稍后重试' });
            } finally {
                this.loading = false;
                this.scrollToBottom();
            }
        },

        clearChat() {
            this.messages = [];
            this.input = '';
        },

        // 把筛选 dict 渲染成一行可读文本；空对象返回空串（调用处据此隐藏）。
        // 同时服务两处：历史消息的「筛选：…」与输入框上方的「本轮生效：…」。
        //
        // ⚠️ 本方法**定义在 T3**（纯展示逻辑，无任何依赖，提前定义不影响任何东西）；
        //    T4 重写组件时**必须原样保留**，删掉它会连带让 filtersChanged() 与
        //    历史消息的「筛选：…」一起失效（本 Task 的 t3_pending_filter_hint_appears_after_change
        //    与 T4 的 t4_filters_recorded_and_shown 都会红）。
        describeFilters(f) {
            const LABELS = {
                dim1_hierarchy: '层级', dim1_industry: '行业', dim1_nature: '性质',
                dim2_stage: '阶段', dim3_usage: '用途', dim4_specialty: '专业',
                dim5_location: '地区', dim6_material: '材料',
                status_filter: '状态', include_non_clause: '含前言说明',
            };
            if (!f || !Object.keys(f).length) return '';
            return Object.entries(f).map(([k, v]) => {
                const name = LABELS[k] || k;
                if (v === true) return name;                       // 布尔开关只显示名字
                if (v === false || v === '' || v == null) return '';  // 未启用/未选不显示
                const val = Array.isArray(v) ? v.join('/') : String(v);
                return val ? `${name}=${val}` : '';
            }).filter(Boolean).join(' · ');
        },

        // 收集本轮筛选（**唯一来源**）：分类维度 + 状态 + 前言放行。
        // buildRequestBody 与 filtersChanged 共用同一份，避免两处各拼一套而漂移。
        collectFilters() {
            const ss = this.$store.searchState;
            const out = { ...ss.filters };
            // buildStatusFilter() 全不勾返回 null → 传空串（不过滤非现行），与既有语义一致
            const sf = this.statusFilter;
            out.status_filter = (sf === null || sf === undefined) ? '' : sf;
            if (ss.includeNonClause) out.include_non_clause = true;
            return out;
        },

        buildRequestBody(question) {
            const body = { question, mode: this.mode, ...this.collectFilters() };
            if (this.currentSessionId) body.session_id = this.currentSessionId;
            return body;
        },

        // 筛选已改但尚未生效（设计文档场景 2 的可见性）：
        // 比较「当前选中」与「本轮实际生效」，不一致就提示——否则用户切了以为生效了。
        filtersChanged() {
            if (!this.effectiveFiltersText) return false;
            return this.describeFilters(this.collectFilters()) !== this.effectiveFiltersText;
        },

        scrollToBottom() {
            this.$nextTick(() => {
                const box = this.$refs.msgBox;
                if (box) box.scrollTop = box.scrollHeight;
            });
        },

        renderMarkdown(text, sources) {
            const md = text || '';
            let html;
            // 复用 md-render.js 完整管线（marked → DOMPurify → KaTeX +
            // 孤立上标修复 + 字面\n→<br>），否则 LaTeX 公式与 HTML div 以源码显示
            if (window.mdRender && typeof window.mdRender.renderHtml === 'function') {
                html = window.mdRender.renderHtml(md, '');
            } else if (typeof marked !== 'undefined') {
                html = marked.parse(md);
            } else {
                return '<pre>' + md + '</pre>';
            }
            // 把正文出处【《规范编号》条文X】转超链接，点击复用 clause-modal 详情弹窗。
            // 兼容：表X 前缀（映射到对应条文）、顿号/逗号分隔的多个条文号（拆开逐个匹配）。
            // 安全：DOMPurify 消毒后回插 DB/用户可控字符串，必须先转义（防消毒后注入绕过）。
            const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
            }[c]));
            if (sources && sources.length) {
                html = html.replace(/【《([^》]+)》([^】]+)】/g, (m, code, clauses) => {
                    const codeT = code.trim();
                    const parts = clauses.split(/[、，,]/).map(s => s.trim()).filter(Boolean);
                    const linked = parts.map(part => {
                        // 「表3.0.5」→ 优先匹配「表3.0.5」，其次匹配条文「3.0.5」（表嵌于该条文）
                        const candidates = [part, part.replace(/^表/, '').trim()];
                        const src = sources.find(s =>
                            s.code === codeT && candidates.indexOf(s.clause_no) !== -1);
                        if (src && src.clause_id) {
                            let warn = '';
                            if (src.status === '废止' || src.replace_by_code) {
                                warn = '<span style="color:#b00000;font-size:0.7rem;margin-left:0.25rem">⚠️' +
                                    (src.replace_by_code ? `已被《${esc(src.replace_by_code)}》替代` : '已废止') + '</span>';
                            }
                            return `<a href="javascript:void(0)" style="color:var(--pico-primary);text-decoration:underline;cursor:pointer" onclick="window.dispatchEvent(new CustomEvent('view-clause',{detail:{id:${src.clause_id}}}))">${esc(part)}</a>${warn}`;
                        }
                        return esc(part);
                    });
                    return `【《${esc(codeT)}》${linked.join('、')}】`;
                });
            }
            return html;
        },

        // 底部「参考条文」区链接点击：打开条文详情弹窗
        openClause(clauseId) {
            if (clauseId) {
                window.dispatchEvent(new CustomEvent('view-clause', { detail: { id: clauseId } }));
            }
        },
    }));
});
