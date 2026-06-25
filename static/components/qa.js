// AI 问答面板组件（带 localStorage 持久化）
document.addEventListener('alpine:init', () => {
    Alpine.data('qaPanel', () => {
        // 从 localStorage 恢复聊天记录
        const STORAGE_KEY = 'qa_messages_v1';
        let savedMessages = [];
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (raw) {
                savedMessages = JSON.parse(raw);
            }
        } catch (e) {
            savedMessages = [];
        }

        return {
            messages: savedMessages,
            question: '',
            loading: false,
            backend: localStorage.getItem('qa_backend') || 'claude',

            _save() {
                try {
                    localStorage.setItem(STORAGE_KEY, JSON.stringify(this.messages));
                    localStorage.setItem('qa_backend', this.backend);
                } catch (e) {
                    // localStorage 满或不可用，静默忽略
                }
            },

            clearMessages() {
                this.messages = [];
                this._save();
            },

            async sendQuestion() {
                if (!this.question.trim()) return;
                const q = this.question;
                this.messages.push({ role: 'user', content: q });
                this._save();
                this.question = '';
                this.loading = true;
                try {
                    const resp = await fetch('/qa/ask', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ question: q, backend: this.backend }),
                    });
                    const data = await resp.json();
                    const answer = data.answer || '抱歉，未获取到有效回复。';
                    this.messages.push({
                        role: 'assistant',
                        content: answer,
                        sources: data.sources || [],
                    });
                    this._save();
                } catch (e) {
                    this.messages.push({
                        role: 'assistant',
                        content: '抱歉，AI 服务暂时不可用。',
                    });
                    this._save();
                } finally {
                    this.loading = false;
                }
            },
        };
    });
});
