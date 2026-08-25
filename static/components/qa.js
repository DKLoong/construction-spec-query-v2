// AI 问答组件
document.addEventListener('alpine:init', () => {
    Alpine.data('qaView', () => ({
        messages: [],
        input: '',
        loading: false,
        mode: 'rag',            // rag 综合问答（默认） / verbatim 原文摘抄

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
                    body: JSON.stringify({ question: q, mode: this.mode, ...filters }),
                });
                const data = await resp.json();
                this.messages.push({
                    role: 'bot',
                    content: data.answer || '(AI 未返回回答)',
                    sources: data.sources || [],
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
            // 把正文出处【《规范编号》条文X】转超链接，点击复用 clause-modal 详情弹窗
            if (sources && sources.length) {
                html = html.replace(/【《([^》]+)》([^】]+)】/g, (m, code, clause_no) => {
                    const src = sources.find(s =>
                        s.code === code.trim() && s.clause_no === clause_no.trim());
                    if (src && src.clause_id) {
                        return `<a href="javascript:void(0)" style="color:var(--pico-primary);text-decoration:underline;cursor:pointer" onclick="window.dispatchEvent(new CustomEvent('view-clause',{detail:{id:${src.clause_id}}}))">${m}</a>`;
                    }
                    return m;
                });
            }
            return html;
        },
    }));
});
