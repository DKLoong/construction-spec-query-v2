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
    // 消息唯一 id：x-for 的 key 需要稳定，避免每次增量渲染重建 DOM 丢滚动位置
    let _uid = 0;

    Alpine.data('qaView', () => ({
        messages: [],
        sessions: [],
        searchHits: [],
        searchKeyword: '',
        input: '',
        loading: false,
        mode: 'rag',                 // rag 综合问答（默认） / verbatim 原文摘抄
        currentSessionId: null,      // null = 草稿态（首次发送才落库建会话）
        // 会话栏折叠态：T1 骨架的「◂ 会话」按钮已引用它
        sessionsCollapsed: false,
        rerankUsed: '',              // 后端上报的精排级别（'' = 未上报，不显示标记）
        stageText: '正在检索…',

        // 本轮实际生效的筛选（D5）：回复中改筛选只影响下一轮，靠它与当前选中比对出提示。
        // **T3 已定义**（连同 describeFilters()/filtersChanged()），此处原样保留：
        // 删掉会让 filtersChanged() 第一行短路 ⇒ 「将在下一轮生效」恒不出现（静默失效）。
        effectiveFiltersText: '',

        // 状态过滤改为读左栏共享 store（统一操作逻辑）：
        // QA 面板内不再有独立复选框，「仅现行 / 修订中」一律由左栏控制
        get statusFilter() {
            return this.$store.searchState.buildStatusFilter();
        },

        async init() {
            // ⚠️ 首行**必须**是 seedFiltersFromUrl()，顺序也不能换（先回填 URL → 再拉会话列表）：
            //    删掉它 ⇒ 筛选无法经 URL 带入 QA 页 / 刷新后丢失
            //    （T1 的 t1_filters_carry_into_qa_via_url、t1_qa_page_filters_survive_reload 回归失败）。
            this.seedFiltersFromUrl();
            await this.loadSessions();
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

        // ── 会话管理 ──

        async loadSessions() {
            try {
                const r = await fetch('/qa/sessions');
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                this.sessions = data.sessions || [];
            } catch (e) {
                console.error('[qa] 加载会话列表失败:', e);
            }
        },

        newSession() {
            // 回到草稿态：首次发送才创建会话（避免空会话堆积）
            this.currentSessionId = null;
            this.messages = [];
            this.input = '';
            this.rerankUsed = '';
        },

        async openSession(sid) {
            if (this.loading) return;
            try {
                const r = await fetch(`/qa/sessions/${sid}`);
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                // 字段结构与 qa_messages 一一对应，直接映射。
                // ⚠️ role 值域是后端的 {user, assistant}（qa_messages.role），**不要**在本地另造
                //    'bot' 之类的别名：模板的助手分支判的就是 `msg.role === 'assistant'`，
                //    写成 'bot' 会让历史会话只显示提问、不显示任何回答。
                // html 必须走完整渲染管线（含 KaTeX/超链接改写），因为流式期间的降级渲染不跑公式。
                this.messages = (data.messages || []).map(m => ({
                    uid: ++_uid,
                    role: m.role,
                    content: m.content,
                    html: m.role === 'assistant'
                        ? this.renderMarkdown(m.content, m.sources || []) : '',
                    sources: m.sources || [],
                    confusable: m.confusable || [],
                    // 当轮筛选由后端落库（qa_messages.filters_json），此处回填
                    // 「这条答案是在什么筛选下产生的」——筛选不入库则该信息不可逆丢失。
                    filtersText: this.describeFilters(m.filters),
                    filteredOut: 0,
                    streaming: false,
                }));
                this.currentSessionId = sid;
                this.scrollToBottom();
            } catch (e) {
                console.error('[qa] 载入会话失败:', e);
            }
        },

        // 导出 Markdown：后端返回带 Content-Disposition 的附件响应，
        // 交给浏览器直接下载，前端无需拼内容
        exportSession(s) {
            window.location.href = `/qa/sessions/${s.id}/export`;
        },

        async renameSession(s) {
            const next = window.prompt('新的会话名称：', s.title);
            if (next === null) return;
            const title = next.trim();
            if (!title) return;
            try {
                const r = await fetch(`/qa/sessions/${s.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title }),
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                await this.loadSessions();
            } catch (e) {
                console.error('[qa] 重命名失败:', e);
            }
        },

        async deleteSession(s) {
            // 删除不可撤销，必须二次确认（开发铁律 1.5）
            if (!window.confirm(`确认删除会话「${s.title}」？该操作不可撤销。`)) return;
            try {
                const r = await fetch(`/qa/sessions/${s.id}`, { method: 'DELETE' });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                if (this.currentSessionId === s.id) this.newSession();
                await this.loadSessions();
            } catch (e) {
                console.error('[qa] 删除失败:', e);
            }
        },

        // ── 跨会话搜索 ──

        async runSearch() {
            const q = this.searchKeyword.trim();
            if (!q) { this.searchHits = []; return; }
            try {
                const r = await fetch(`/qa/search?q=${encodeURIComponent(q)}`);
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                this.searchHits = data.hits || [];
            } catch (e) {
                console.error('[qa] 搜索失败:', e);
            }
        },

        async jumpToHit(hit) {
            await this.openSession(hit.session_id);
            this.searchHits = [];
            this.searchKeyword = '';
            // 定位到命中消息：等 DOM 渲染完成后滚动 + 短暂高亮
            this.$nextTick(() => {
                const idx = this.messages.findIndex(m => m.content === hit.content);
                if (idx < 0) return;
                const nodes = this.$refs.msgBox.querySelectorAll('.qa-msg');
                const el = nodes[idx];
                if (el) {
                    el.scrollIntoView({ block: 'center' });
                    el.classList.add('qa-highlight');
                    setTimeout(() => el.classList.remove('qa-highlight'), 2000);
                }
            });
        },

        toggleMode() {
            this.mode = (this.mode === 'rag') ? 'verbatim' : 'rag';
        },

        // 生成期间的渲染辅助：只做 HTML 转义，不解析 Markdown。
        // 换行由 CSS 的 white-space: pre-wrap 呈现（见 app.css 的 .qa-answer.streaming）。
        _streamingHtml(text) {
            return String(text ?? '').replace(/[&<>"']/g, c => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
            }[c]));
        },

        // ── 提问 ──

        // opts.relaxed：本次为「放宽分类筛选」的重发（只忽略分类维度）
        // opts.question：重发时用的原问题（不传则读输入框）
        async send(opts = {}) {
            const fromInput = opts.question === undefined;
            const q = (fromInput ? this.input : String(opts.question)).trim();
            if (!q || this.loading) return;
            this.messages.push({ uid: ++_uid, role: 'user', content: q, html: '',
                                 sources: [], confusable: [], filtersText: '',
                                 filteredOut: 0, streaming: false });
            if (fromInput) this.input = '';
            this.loading = true;
            this.stageText = '正在检索…';
            this.scrollToBottom();

            try {
                // 统一由 buildRequestBody() 组合请求体（读共享 store；QA 面板内不再自持筛选状态），
                // 并携带 currentSessionId ⇒ 在历史会话里追问会**追加到同一会话**。
                const body = this.buildRequestBody(q);
                if (opts.relaxed) body.relaxed = true;
                const resp = await fetch('/qa/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                // 写入本轮生效筛选：否则 .qa-filters-pending（「将在下一轮生效」）恒不出现
                // （后端 /qa/ask 的 JSON 响应已带 effective_filters）
                this.effectiveFiltersText = this.describeFilters(data.effective_filters);
                if (data.rerank_used) this.rerankUsed = data.rerank_used;
                // 落库后的会话 id（草稿态首轮发送即在此创建会话）
                if (data.session_id) this.currentSessionId = data.session_id;
                // 先刷新会话列表再补消息：列表项与消息同帧可见，探针/用户不会看到
                // 「回答已出现但会话还没进列表」的中间态
                await this.loadSessions();

                const answer = data.answer || '(AI 未返回回答)';
                // role 值域是后端的 {user, assistant}（qa_messages.role），**不要**在本地另造 'bot' 别名：
                // 新模板的助手分支判的就是 `msg.role === 'assistant'`，写成 'bot' 会让每条回答都渲染成空气泡。
                // 同时必须给 `html`（模板用 x-html="msg.html" 渲染回答）。
                this.messages.push({
                    uid: ++_uid,
                    role: 'assistant',
                    content: answer,
                    html: this.renderMarkdown(answer, data.sources || []),
                    sources: data.sources || [],
                    confusable: data.confusable_hits || [],
                    filtersText: this.describeFilters(data.effective_filters),
                    filteredOut: data.filtered_out || 0,
                    streaming: false,
                });
            } catch (e) {
                console.error('[qa] 提问失败:', e);
                // 失败提示同样要给 html（固定字面量、无用户内容，无需转义）
                this.messages.push({ uid: ++_uid, role: 'assistant',
                                     content: '请求失败，请稍后重试',
                                     html: '请求失败，请稍后重试',
                                     sources: [], confusable: [], filtersText: '',
                                     filteredOut: 0, streaming: false });
            } finally {
                this.loading = false;
                this.scrollToBottom();
            }
        },

        // 「放宽分类筛选」：用同一个问题重发一次（只忽略分类维度，状态/前言设置仍生效）。
        // 必须复用 send()，不另开一条取数路径——否则两条路径的收尾逻辑必然漂移。
        relax() {
            const lastUser = [...this.messages].reverse().find(m => m.role === 'user');
            if (!lastUser) return;
            this.send({ relaxed: true, question: lastUser.content });
        },

        // ── 筛选可读化 ──

        // describeFilters(f) / collectFilters() / buildRequestBody(question) / filtersChanged()
        // 四个方法**定义在 T3**（唯一定义点，含完整实现与 LABELS 常量表），此处原样保留：
        // 删掉/改写会让 T3 的 t3_pending_filter_hint_appears_after_change 与
        // T4 的 t4_filters_recorded_and_shown 一起红。

        // 把筛选 dict 渲染成一行可读文本；空对象返回空串（调用处据此隐藏）。
        // 同时服务两处：历史消息的「筛选：…」与输入框上方的「本轮生效：…」。
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
            // 续聊语义：带上当前会话 ⇒ 后端把本轮追加到该会话而不是新建
            if (this.currentSessionId) body.session_id = this.currentSessionId;
            return body;
        },

        // 筛选已改但尚未生效（设计文档场景 2 的可见性）：
        // 比较「当前选中」与「本轮实际生效」，不一致就提示——否则用户切了以为生效了。
        filtersChanged() {
            if (!this.effectiveFiltersText) return false;
            return this.describeFilters(this.collectFilters()) !== this.effectiveFiltersText;
        },

        // ── 降级状态提示 ──

        // ⚠️ 必须显式判 'crossencoder' / 'vector' / 'none' 三态。
        // 后端 QAResponse.rerank_used 的缺省是空串——那是**契约外的第四态**（后端未上报），
        // 不是 RERANK_NONE。用 `else → 无精排` 的写法会在字段只是没上报时谎报降级。
        rerankBadge() {
            if (this.rerankUsed === 'crossencoder') return '⚡ CE 精排';
            if (this.rerankUsed === 'vector') return '≈ 向量精排';
            if (this.rerankUsed === 'none') return '⚠️ 无精排';
            return '';   // 未上报 → 不显示标记（模板的 x-show="rerankUsed" 会隐藏它）
        },

        rerankTip() {
            if (this.rerankUsed === 'crossencoder') return 'CrossEncoder 精排生效';
            if (this.rerankUsed === 'vector') return 'CE 模型不可用，已降级为向量精排';
            if (this.rerankUsed === 'none') {
                return '未检测到精排模型，当前按关键词排序；'
                     + '请在维护页「健康检查」查看缺失的模型';
            }
            return '';   // 未上报 → 无提示
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
