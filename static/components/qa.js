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
                this.messages.push({ role: 'bot', content: data.answer || '(AI 未返回回答)' });
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

        renderMarkdown(text) {
            const md = text || '';
            // 复用 md-render.js 完整管线（marked → DOMPurify → KaTeX +
            // 孤立上标修复 + 字面\n→<br>），否则 LaTeX 公式与 HTML div 以源码显示
            if (window.mdRender && typeof window.mdRender.renderHtml === 'function') {
                return window.mdRender.renderHtml(md, '');
            }
            if (typeof marked !== 'undefined') {
                return marked.parse(md);
            }
            return '<pre>' + md + '</pre>';
        },
    }));
});
