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

// HTML 转义：任何把用户/DB 可控字符串插进 HTML 的路径（流式期间的纯文本渲染、
// renderMarkdown 的源链接改写）都必须先转义（开发铁律 1.1）。
// 抽成模块级**单一来源**：两处各写一份迟早漂移。
function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

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
        // 重命名内联态：为哪条会话开着输入框 + 草稿值（见 startRename/commitRename）
        renamingId: null,
        renameDraft: '',
        // 删除确认态（页内确认，不用 window.confirm——同 prompt：原生对话框会被静默抑制）
        confirmingDeleteId: null,
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
            // 会话级**显示**状态必须一并复位：
            //  - effectiveFiltersText 是上一会话的「本轮生效」基线，留着会让输入框上方常挂
            //    一条（很可能已不成立的）「本轮生效：…」，且 filtersChanged() 会拿这个
            //    **过时基线**比对当前选中 ⇒「已修改，将在下一轮生效」在新会话里
            //    虚假出现或该出现却不出现（探针 t4_new_session_resets_session_scoped_display）。
            //  - rerankUsed / stageText 同理（T5 起 stageText 会在流式期间被改写）。
            this.effectiveFiltersText = '';
            this.rerankUsed = '';
            this.stageText = '正在检索…';
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

        // ── 重命名：页内内联输入（**不用 window.prompt**）──
        // 为什么不用原生对话框：它在部分浏览器设置/扩展下会被**静默抑制**
        // ⇒ 用户点 ✎「没反应」，且没有任何报错可查（真实用户实测反馈）。
        // 内联输入还顺带避免了打断操作流、并让新名字在原位可见。

        startRename(s) {
            this.renamingId = s.id;
            this.renameDraft = s.title;
            // 聚焦/全选交给 input 自己的 `x-init`（模板里）：
            // 实测 `$nextTick` 在 x-if 重新渲染之前就跑了 ⇒ 元素还没进 DOM，focus() 落空
            // （浏览器实机验证抓到：输入框出现但未聚焦、也未全选）。
        },

        cancelRename() {
            this.renamingId = null;      // 置空即移除 input（x-if）⇒ 随后的 blur 会被下面的守卫挡掉
            this.renameDraft = '';
        },

        async commitRename(s) {
            // 守卫：Enter 提交后 input 被移除会再触发一次 blur；Esc 取消后的 blur 也走这里
            if (this.renamingId !== s.id) return;
            const title = (this.renameDraft || '').trim();
            this.renamingId = null;
            this.renameDraft = '';
            // 空名或没改：静默取消（保持原标题），不发请求
            if (!title || title === s.title) return;
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

        // ── 删除：页内二次确认（**不用 window.confirm**）──
        // 删除不可撤销，必须二次确认（开发铁律 1.5），但确认**不能依赖原生对话框**：
        // 它与 prompt 同类，会被部分浏览器设置/扩展静默抑制 ⇒ 用户点 🗑 毫无反应（且无报错可查）。
        startDelete(s) {
            this.renamingId = null;          // 与重命名互斥，避免同一行同时开两个态
            this.renameDraft = '';
            this.confirmingDeleteId = s.id;
        },

        cancelDelete() {
            this.confirmingDeleteId = null;
        },

        async doDelete(s) {
            if (this.confirmingDeleteId !== s.id) return;   // 守卫：与 commitRename 同款
            this.confirmingDeleteId = null;
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

        // ⚠️ **不要**写 `this.$refs.msgBox`：**方法内取不到 `$refs`**。
        //    现象已复现（qa.js 旧写法 + T5 探针）：jumpToHit 下一行直接抛
        //    `TypeError: Cannot read properties of undefined (reading 'querySelectorAll')`
        //    ⇒ 高亮从未真正加上过（不是"样式缺失所以看不见"，是类压根没加上）。
        //    **机制未定**（勿再写成结论）：「Alpine 的 magic（`$refs`/`$root`）解析上下文 =
        //    调用该方法的元素」这一解释**已被复核证伪**——项目自带的 alpine.min.js(3.15.12)
        //    里 `$refs` 走 `_x_refs_proxy`，而 `_x_refs` 只由 `x-ref` 指令建于 closestRoot，
        //    复核者的独立页面实测 `this.$refs.msgBox` **取得到**；同一调用点的
        //    `this.$nextTick` 解析也正常（自相矛盾）。
        //    故不再依赖 `$refs` 在 `x-if` / `x-for` 生成元素上的行为，改为**按类名取**：
        //    与调用点无关；QA 页全页只有一个 .qa-messages
        //    （t2_layout_structure_present 断言其唯一性）。
        _messageBox() {
            return document.querySelector('.qa-messages');
        },

        async jumpToHit(hit) {
            await this.openSession(hit.session_id);
            this.searchHits = [];
            this.searchKeyword = '';
            // 定位到命中消息：等 DOM 渲染完成后滚动 + 短暂高亮
            this.$nextTick(() => {
                const idx = this.messages.findIndex(m => m.content === hit.content);
                if (idx < 0) return;
                const box = this._messageBox();
                if (!box) return;
                const el = box.querySelectorAll('.qa-msg')[idx];
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
            return escHtml(text);
        },

        // ── 提问 ──

        // opts.relaxed：本次为「放宽分类筛选」的重发（只忽略分类维度）
        // opts.question：重发时用的原问题（不传则读输入框）
        async send(opts = {}) {
            const fromInput = opts.question === undefined;
            const q = (fromInput ? this.input : String(opts.question)).trim();
            if (!q || this.loading) return;

            // 统一由 buildRequestBody() 组合请求体（读共享 store；QA 面板内不再自持筛选状态），
            // 并携带 currentSessionId ⇒ 在历史会话里追问会**追加到同一会话**。
            const body = this.buildRequestBody(q);
            if (opts.relaxed) body.relaxed = true;

            // 乐观插入用户消息（失败时回滚，见 _fallbackAsk 的 catch 分支）
            const userMsg = { uid: ++_uid, role: 'user', content: q, html: '',
                              sources: [], confusable: [], filtersText: '',
                              filteredOut: 0, streaming: false };
            this.messages.push(userMsg);
            if (fromInput) this.input = '';
            this.loading = true;
            this.rerankUsed = '';
            this.stageText = '正在检索…';
            this.scrollToBottom();

            // 助手气泡**先占位**：`.streaming` 决定它走「转义纯文本 + pre-wrap」的降级样式
            // （收尾时置回 false 并跑完整渲染管线）。
            // role 值域是后端的 {user, assistant}（qa_messages.role），**不要**在本地另造 'bot' 别名：
            // 模板的助手分支判的就是 `msg.role === 'assistant'`，写成 'bot' 会让每条回答都渲染成空气泡。
            // 同时必须给 `html`（模板用 x-html="msg.html" 渲染回答）。
            this.messages.push({
                uid: ++_uid, role: 'assistant', content: '', html: '',
                sources: [], confusable: [], filteredOut: 0,
                filtersText: '', streaming: true,
            });
            const bot = this.messages[this.messages.length - 1];

            try {
                await this._streamAsk(body, bot);
            } catch (e) {
                // **仅**传输层失败才走兜底：`fetch` 抛错 / 响应非 2xx / `resp.body` 缺失 /
                // reader 读流中断。应用层错误由 SSE 的 `error` 帧承载，已在 _handleSseChunk
                // 内就地展示（那里**不 throw**）⇒ 不会走到这里、不会重发同一问题。
                console.error('[qa] 流式请求失败（传输层），回退非流式:', e);
                await this._fallbackAsk(body, bot, userMsg);
            } finally {
                this.loading = false;
                // 收尾：用完整管线（含 KaTeX）重渲染一次，公式在此时排版
                bot.streaming = false;
                if (bot.content) bot.html = this.renderMarkdown(bot.content, bot.sources);
                this.scrollToBottom();
                await this.loadSessions();
            }
        },

        // SSE 读取。不用 EventSource：它只支持 GET，而我们需要 POST body
        // （问题 + 筛选 + session_id）。
        // 与兜底路径同一 URL，仅以 stream 标志区分响应形态（后端为单一入口）。
        async _streamAsk(body, bot) {
            const resp = await fetch('/qa/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...body, stream: true }),
            });
            if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

            const reader = resp.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buf = '';

            for (;;) {
                // 读流中断（网络断开）会从 read() 抛出 ⇒ 冒到 send() 的 catch 走兜底，
                // 这是**传输层**失败，与 SSE 的 error 帧（应用层）语义不同。
                const { done, value } = await reader.read();
                if (done) break;
                buf += decoder.decode(value, { stream: true });
                // SSE 以空行分隔消息；保留最后一段不完整数据在 buf 中。
                // ⚠️ 分帧必须用 `/\r?\n\r?\n/` 而不是字面量 `'\n\n'`：当前后端发的是 LF
                //    （两者等价），但只要中间有一层反向代理把行尾改写成 CRLF，字面量就**永不切分**
                //    ⇒ buf 无限增长、末尾残包被丢弃 ⇒ **静默无输出且无告警**。成本极低，直接兼容。
                //    （每行的行尾 `\r` 由下方 `_handleSseChunk` 里的 `trim()` 吃掉。）
                const chunks = buf.split(/\r?\n\r?\n/);
                buf = chunks.pop() || '';
                for (const chunk of chunks) this._handleSseChunk(chunk, bot);
            }
        },

        _handleSseChunk(chunk, bot) {
            let event = 'message';
            const dataLines = [];
            for (const line of chunk.split('\n')) {
                if (line.startsWith('event:')) event = line.slice(6).trim();
                else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
            }
            if (!dataLines.length) return;
            let data;
            try {
                data = JSON.parse(dataLines.join('\n'));
            } catch (e) {
                // 单帧坏数据不该毁掉整轮回答：跳过该帧、留下告警（保留原始错误）
                console.warn('[qa] SSE 数据解析失败，跳过该帧:', e);
                return;
            }

            if (event === 'stage') {
                // ⚠️ 后端 stage **只有两态**（`reranking` 已在施工中删除：检索与 CE 精排都在同一个
                // 同步函数内、无法从里面 yield，拆两帧是假装能区分它们）。
                // 保留一个「处理中…」兜底，但**不要**再加回 `reranking` 分支。
                this.stageText = {
                    retrieving: '正在检索…',
                    generating: '生成中…',
                }[data.stage] || '处理中…';
            } else if (event === 'delta') {
                // ⚠️ **不要 throw**（同 error 分支的理由）：本方法由 _streamAsk 调用，
                //    这里抛错会一路冒到 send() 的 catch → 被**误判为传输层失败** →
                //    _fallbackAsk **重发同一个问题**。故整段包 try/catch：出错只记录、**继续**。
                try {
                    bot.content += data.text || '';
                    // 生成期间只做转义纯文本，**不跑任何 Markdown 解析器**：半截 Markdown
                    // （未闭合的 ** / 表格 / 公式）会让解析器反复重排，比
                    // 「纯文本 → 一次性排版」更晃眼；KaTeX 遇到未闭合公式还会渲染失败甚至抛错。
                    bot.html = this._streamingHtml(bot.content);
                    this.scrollToBottom();
                } catch (e) {
                    console.error('[qa] 流式增量渲染失败（继续读后续帧，不重发）:', e);
                }
            } else if (event === 'done') {
                // 同上：`describeFilters` / `scrollToBottom` 任一处抛错都**不得**冒到
                // send() 的 catch（那会被当成传输层失败而重发同一问题——正是 error 分支
                // 注释要避免的重复请求）。捕获后只记录，本轮按已收到的数据收尾。
                try {
                    bot.sources = data.sources || [];
                    bot.confusable = data.confusable_hits || [];
                    bot.filteredOut = data.filtered_out || 0;
                    this.rerankUsed = data.rerank_used || '';
                    this.currentSessionId = data.session_id || this.currentSessionId;
                    // 写入本轮生效筛选：否则 .qa-filters-pending（「将在下一轮生效」）恒不出现
                    const ft = this.describeFilters(data.effective_filters);
                    bot.filtersText = ft;
                    this.effectiveFiltersText = ft;
                } catch (e) {
                    console.error('[qa] done 帧写入失败（不重发，按已收数据收尾）:', e);
                }
            } else if (event === 'error') {
                // ⚠️ **不要 `throw`**！_handleSseChunk 由 _streamAsk 调用，throw 会一路冒到
                // send() 的 catch → 立刻走 _fallbackAsk **重发同一个问题**：后端已经报错
                // （未配置后端 / 模型不可用）时这是白等一轮，且用户看到**两次完整生成**
                // （重复计费、重复落库）。
                // 边界：SSE 的 `error` 事件是**应用层**错误（后端已受理请求并显式回了 error 帧），
                // ⇒ 只展示与记录，**不进兜底分支**。
                // `_fallbackAsk` 只服务**传输层失败**（`fetch` 抛错 / `resp.body` 缺失 /
                // reader 读流中断）——见 send() 的 catch 注释。
                bot.content = data.message || 'AI 服务返回错误';
                console.error('[qa] AI 服务返回错误（不再自动重发）:', data.message);
            }
        },

        // 流式失败兜底：同一 URL 重发，只是不带 stream → 走一次性 JSON。功能不丢。
        // **只用于传输层失败**（fetch 抛错 / 读流异常），不作为应用层错误的补救路径。
        async _fallbackAsk(body, bot, userMsg) {
            try {
                const r = await fetch('/qa/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ...body, stream: false }),
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                bot.content = data.answer || '(AI 未返回回答)';
                bot.sources = data.sources || [];
                bot.confusable = data.confusable_hits || [];
                bot.filteredOut = data.filtered_out || 0;
                this.rerankUsed = data.rerank_used || '';
                this.currentSessionId = data.session_id || this.currentSessionId;
                const ft = this.describeFilters(data.effective_filters);
                bot.filtersText = ft;
                this.effectiveFiltersText = ft;
            } catch (e2) {
                console.error('[qa] 非流式兜底同样失败:', e2);
                // 失败文案（探针 FAIL_ANSWER_TEXT 所指的**唯一来源**字面量）：
                // 该气泡在下一行就被回滚，故这行只用于失败语义留痕，不参与渲染。
                bot.content = '请求失败，请稍后重试';
                // 本轮彻底失败 ⇒ **用户消息与乐观插入的助手气泡一并回滚**。
                // 只回滚用户消息的话，界面上会留下一条**没有对应问题**的
                // 「请求失败，请稍后重试」气泡（孤儿气泡：用户看不到自己问过什么，
                // 也不知道为什么冒出这条），与回滚用户消息的语义不一致。
                this.messages = this.messages.filter(
                    m => m.uid !== userMsg.uid && m.uid !== bot.uid);
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
                // 同 jumpToHit：改为按类名取（`$refs` 在方法内的取用行为**未定**，见
                // _messageBox() 的注释；原先"magic 解析上下文=调用点元素 ⇒ 从未真正贴底"
                // 的说法已被复核证伪，不再保留）。旧写法 `this.$refs.msgBox` + `if (box)`
                // 在取不到时是**静默空操作**（贴没贴底无从判断）；流式期间每帧都要贴底，
                // 不能留这种可能。
                const box = this._messageBox();
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
            // 转义用模块级 escHtml（单一来源，与 _streamingHtml 同一实现）。
            const esc = escHtml;
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
