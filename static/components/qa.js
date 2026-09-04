// AI 问答组件
document.addEventListener('alpine:init', () => {
    Alpine.data('qaView', () => ({
        messages: [],
        input: '',
        loading: false,
        mode: 'rag',            // rag 综合问答（默认） / verbatim 原文摘抄
        // 状态过滤：默认仅勾「现行」（与检索侧 tree.js 语义一致，全不勾=不过滤非现行）
        statusCurrent: true,
        statusRevising: false,

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
