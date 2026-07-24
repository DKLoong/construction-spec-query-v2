document.addEventListener('alpine:init', () => {
    Alpine.data('settingsDialog', () => ({
        open: false,
        activeTab: 'ocr',

        // OCR
        ocrToken: '',
        ocrTesting: false,
        ocrTestResult: '',

        // AI
        aiBackend: 'claude',
        aiKeys: {},
        aiTesting: false,
        aiTestResult: '',

        init() {
            window.addEventListener('open-settings', (e) => {
                this.activeTab = e.detail?.tab || 'ocr';
                this.open = true;
                this.loadSettings();
            });
        },

        async loadSettings() {
            try {
                const resp = await fetch('/settings');
                const data = await resp.json();
                this.ocrToken = data['ocr.access_token'] || '';
                this.aiBackend = data['ai.backend'] || 'claude';
                this.aiKeys = {
                    doubao: data['ai.doubao.api_key'] || '',
                    deepseek: data['ai.deepseek.api_key'] || '',
                    glm: data['ai.glm.api_key'] || '',
                    kimi: data['ai.kimi.api_key'] || '',
                    custom_base_url: data['ai.custom.base_url'] || '',
                    custom_api_key: data['ai.custom.api_key'] || '',
                    custom_model: data['ai.custom.model'] || '',
                };
            } catch (e) {
                console.error('加载设置失败', e);
            }
        },

        async save() {
            const payload = {
                'ocr.access_token': this.ocrToken,
                'ai.backend': this.aiBackend,
                'ai.doubao.api_key': this.aiKeys.doubao,
                'ai.deepseek.api_key': this.aiKeys.deepseek,
                'ai.glm.api_key': this.aiKeys.glm,
                'ai.kimi.api_key': this.aiKeys.kimi,
                'ai.custom.base_url': this.aiKeys.custom_base_url,
                'ai.custom.api_key': this.aiKeys.custom_api_key,
                'ai.custom.model': this.aiKeys.custom_model,
            };
            try {
                await fetch('/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                this.open = false;
            } catch (e) {
                console.error('保存设置失败', e);
            }
        },

        async testOCR() {
            this.ocrTesting = true;
            this.ocrTestResult = '';
            try {
                const resp = await fetch('/settings/test-ocr', { method: 'POST' });
                const data = await resp.json();
                if (resp.ok) {
                    this.ocrTestResult = '✅ ' + data.message;
                } else {
                    this.ocrTestResult = '❌ ' + (data.detail || '未知错误');
                }
            } catch (e) {
                this.ocrTestResult = '❌ 网络错误';
            }
            this.ocrTesting = false;
        },

        async testAI() {
            this.aiTesting = true;
            this.aiTestResult = '';
            const backend = this.aiBackend;
            let apiKey, baseUrl, model;

            if (backend === 'custom') {
                apiKey = this.aiKeys.custom_api_key;
                baseUrl = this.aiKeys.custom_base_url;
                model = this.aiKeys.custom_model;
            } else if (['doubao', 'deepseek', 'glm', 'kimi'].includes(backend)) {
                apiKey = this.aiKeys[backend];
            }

            try {
                const resp = await fetch('/settings/test-ai', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ backend, api_key: apiKey, base_url: baseUrl, model }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    this.aiTestResult = '✅ ' + data.message;
                } else {
                    this.aiTestResult = '❌ ' + (data.detail || '未知错误');
                }
            } catch (e) {
                this.aiTestResult = '❌ 网络错误';
            }
            this.aiTesting = false;
        },

        close() {
            this.open = false;
            this.ocrTestResult = '';
            this.aiTestResult = '';
        },

        // 当前后端对应的 api_key (用于绑定输入框)
        get currentAIKey() {
            if (this.aiBackend === 'custom') return this.aiKeys.custom_api_key;
            if (['doubao', 'deepseek', 'glm', 'kimi'].includes(this.aiBackend)) {
                return this.aiKeys[this.aiBackend];
            }
            return '';
        },
        set currentAIKey(val) {
            if (this.aiBackend === 'custom') this.aiKeys.custom_api_key = val;
            else if (['doubao', 'deepseek', 'glm', 'kimi'].includes(this.aiBackend)) {
                this.aiKeys[this.aiBackend] = val;
            }
        },
    }));
});
