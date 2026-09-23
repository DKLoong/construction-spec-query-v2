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
        // 状态过滤：默认仅勾「现行」（与检索侧 tree.js 语义一致，全不勾=不过滤非现行）
        statusCurrent: true,
        statusRevising: false,
        // 会话栏折叠态：T1 骨架的「◂ 会话」按钮已引用它；完整会话栏由 T4 填充
        sessionsCollapsed: false,

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
                // 携带当前分类树选中维度，收窄检索范围提升精确度（未选分类时空对象不影响）
                const filters = (this.$store && this.$store.searchState)
                    ? this.$store.searchState.filters
                    : {};
                const resp = await fetch('/qa/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        question: q, mode: this.mode, ...filters,
                        status_filter: this.buildStatusFilter(),
                    }),
                });
                const data = await resp.json();
                this.messages.push({
                    role: 'bot',
                    content: data.answer || '(AI 未返回回答)',
                    sources: data.sources || [],
                    confusable: data.confusable_hits || [],
                });
            } catch (e) {
                this.messages.push({ role: 'bot', content: '请求失败，请稍后重试' });
            } finally {
                this.loading = false;
                this.scrollToBottom();
            }
        },

        clearChat() {
            this.messages = [];
            this.input = '';
        },

        // 组合状态过滤白名单（与检索侧 tree.js buildStatusFilter 语义一致）：
        // 现行+修订中 → '现行,修订中'；仅现行 → '现行'；仅修订中 → '修订中'；全不勾 → ''（不过滤）
        buildStatusFilter() {
            if (this.statusCurrent && this.statusRevising) return '现行,修订中';
            if (this.statusCurrent) return '现行';
            if (this.statusRevising) return '修订中';
            return '';
        },
        onStatusChange() {},

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
