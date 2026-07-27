document.addEventListener('alpine:init', () => {
    Alpine.data('settingsDialog', () => ({
        open: false,
        activeTab: 'ocr',

        // OCR
        ocrBackend: 'paddle-vl',
        ocrToken: '',               // 通用 access_token（向后兼容，所有后端共用）
        ocrKeys: {                   // 各后端独立 token
            'paddle-vl': '',
            'accurate-basic': '',
            custom_access_token: '',
            custom_base_url: '',
            custom_model: '',
            custom_params: '{}',
        },
        ocrPaddleVLParams: {        // PaddleOCR-VL 高级选项
            analysis_chart: true,
            merge_tables: true,
            relevel_titles: true,
            recognize_seal: false,
        },
        ocrTesting: false,
        ocrTestResult: '',

        // AI
        aiBackend: 'claude',
        aiBackendQA: '',
        aiBackendClassify: '',
        aiKeys: {},
        aiTesting: false,
        aiTestResult: '',

        // 保存状态
        saveError: '',

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
                this.ocrBackend = data['ocr.backend'] || 'paddle-vl';
                this.ocrKeys = {
                    'paddle-vl': data['ocr.paddle-vl.access_token'] || data['ocr.access_token'] || '',
                    'accurate-basic': data['ocr.accurate-basic.access_token'] || data['ocr.access_token'] || '',
                    custom_access_token: data['ocr.custom.access_token'] || '',
                    custom_base_url: data['ocr.custom.base_url'] || '',
                    custom_model: data['ocr.custom.model'] || '',
                    custom_params: data['ocr.custom.params'] || '{}',
                };
                // PaddleOCR-VL 高级参数
                const vlParamsStr = data['ocr.paddle-vl.params'] || '';
                if (vlParamsStr) {
                    try { Object.assign(this.ocrPaddleVLParams, JSON.parse(vlParamsStr)); } catch (e) {}
                }
                this.aiBackend = data['ai.backend'] || 'claude';
                this.aiBackendQA = data['ai.backend.qa'] || '';
                this.aiBackendClassify = data['ai.backend.classify'] || '';
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
            this.saveError = '';
            const payload = {
                'ocr.access_token': this.ocrToken,
                'ocr.backend': this.ocrBackend,
                'ocr.paddle-vl.access_token': this.ocrKeys['paddle-vl'],
                'ocr.paddle-vl.params': JSON.stringify(this.ocrPaddleVLParams),
                'ocr.accurate-basic.access_token': this.ocrKeys['accurate-basic'],
                'ocr.custom.access_token': this.ocrKeys.custom_access_token,
                'ocr.custom.base_url': this.ocrKeys.custom_base_url,
                'ocr.custom.model': this.ocrKeys.custom_model,
                'ocr.custom.params': this.ocrKeys.custom_params,
                'ai.backend': this.aiBackend,
                'ai.backend.qa': this.aiBackendQA,
                'ai.backend.classify': this.aiBackendClassify,
                'ai.doubao.api_key': this.aiKeys.doubao,
                'ai.deepseek.api_key': this.aiKeys.deepseek,
                'ai.glm.api_key': this.aiKeys.glm,
                'ai.kimi.api_key': this.aiKeys.kimi,
                'ai.custom.base_url': this.aiKeys.custom_base_url,
                'ai.custom.api_key': this.aiKeys.custom_api_key,
                'ai.custom.model': this.aiKeys.custom_model,
            };
            try {
                const resp = await fetch('/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (resp.ok) {
                    this.open = false;
                } else {
                    const data = await resp.json().catch(() => ({}));
                    this.saveError = data.detail || '保存失败，请稍后重试';
                }
            } catch (e) {
                this.saveError = '网络连接失败，请检查网络后重试';
            }
        },

        async testOCR() {
            this.ocrTesting = true;
            this.ocrTestResult = '';
            try {
                const resp = await fetch('/settings/test-ocr', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        backend: this.ocrBackend,
                        access_token: this.currentOCRToken,
                    }),
                });
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

        toggleBackendQA(backend) {
            this.aiBackendQA = this.aiBackendQA === backend ? '' : backend;
        },
        toggleBackendClassify(backend) {
            this.aiBackendClassify = this.aiBackendClassify === backend ? '' : backend;
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

        // OCR 当前后端对应的 access_token（类似 currentAIKey）
        get currentOCRToken() {
            if (this.ocrBackend === 'custom') return this.ocrKeys.custom_access_token;
            if (this.ocrBackend === 'paddle-vl') return this.ocrKeys['paddle-vl'];
            if (this.ocrBackend === 'accurate-basic') return this.ocrKeys['accurate-basic'];
            return this.ocrToken;  // 向后兼容
        },
        set currentOCRToken(val) {
            if (this.ocrBackend === 'custom') this.ocrKeys.custom_access_token = val;
            else if (this.ocrBackend === 'paddle-vl') this.ocrKeys['paddle-vl'] = val;
            else if (this.ocrBackend === 'accurate-basic') this.ocrKeys['accurate-basic'] = val;
        },
    }));
});
