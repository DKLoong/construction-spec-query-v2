// AI 问答面板组件
document.addEventListener('alpine:init', () => {
    Alpine.data('qaPanel', () => ({
        messages: [],
        question: '',
        loading: false,
        backend: 'claude',

        async sendQuestion() {
            if (!this.question.trim()) return;
            const q = this.question;
            this.messages.push({ role: 'user', content: q });
            this.question = '';
            this.loading = true;
            try {
                const resp = await fetch('/qa/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question: q, backend: this.backend }),
                });
                const data = await resp.json();
                this.messages.push({ role: 'assistant', content: data.answer, sources: data.sources });
            } catch (e) {
                this.messages.push({ role: 'assistant', content: '抱歉，AI 服务暂时不可用。' });
            } finally {
                this.loading = false;
            }
        },
    }));
});
