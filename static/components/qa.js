// AI 问答组件
document.addEventListener('alpine:init', () => {
    Alpine.data('qaView', () => ({
        messages: [],
        input: '',
        loading: false,

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
                    body: JSON.stringify({ question: q, ...filters }),
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
            if (typeof marked !== 'undefined') {
                return marked.parse(text || '');
            }
            return '<pre>' + (text || '') + '</pre>';
        },
    }));
});
