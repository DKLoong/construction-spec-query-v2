// 导入对话框组件
document.addEventListener('alpine:init', () => {
    Alpine.data('importDialog', () => ({
        open: false,
        uploading: false,
        // 记录输入框当前值是否来自自动识别（区分自动填充 vs 用户手动输入）
        autoFilled: { code: false, title: false },
        // 校核相关状态：codeInput/titleInput 为当前输入框内容，checkResult 为校核结果
        codeInput: '',
        titleInput: '',
        checking: false,
        checkResult: null,   // {status, replacedBy, ai_available, corrected: {code,title}|null}
        contentEdited: false,

        openDialog() { this.open = true; },
        closeDialog() { this.open = false; this.uploading = false; },

        // 用户手动编辑输入框时清除自动填充标记：后续选文件不再覆盖手动输入
        markManual(field) {
            if (field === 'code') this.codeInput = document.querySelector('input[name=code]').value;
            if (field === 'title') this.titleInput = document.querySelector('input[name=title]').value;
            this.autoFilled[field] = false;
            this.contentEdited = true;  // 修改后提示重新校核（不自动触发）
        },

        // 选择文件后自动识别规范编号/名称并回填（不匹配通用命名格式则跳过，交用户手动录入）
        async autoFillFromFilename(event) {
            const file = event.target.files && event.target.files[0];
            if (!file) return;
            const form = event.target.closest('form');
            const codeInput = form ? form.querySelector('input[name="code"]') : null;
            const titleInput = form ? form.querySelector('input[name="title"]') : null;
            if (!codeInput || !titleInput) return;
            // 每个字段仅当「为空」或「当前值是自动填充的」才重新识别覆盖：
            // 选错文件后再次选择可纠正，而用户手动输入过的字段保持不动
            const wantCode = !codeInput.value.trim() || this.autoFilled.code;
            const wantTitle = !titleInput.value.trim() || this.autoFilled.title;
            if (!wantCode && !wantTitle) return;
            try {
                const fd = new FormData();
                fd.append('filename', file.name);
                const resp = await fetch('/import/parse-filename', { method: 'POST', body: fd });
                const data = await resp.json();
                if (data && data.matched) {
                    if (wantCode && data.code) {
                        codeInput.value = data.code;
                        this.autoFilled.code = true;
                        this.codeInput = data.code;  // 同步 Alpine 状态，供校核读取
                    }
                    if (wantTitle && data.title) {
                        titleInput.value = data.title;
                        this.autoFilled.title = true;
                        this.titleInput = data.title;  // 同步 Alpine 状态，供校核读取
                    }
                }
            } catch (e) {
                // 识别接口异常静默忽略，不阻断用户手动录入
            }
        },

        async handleUpload(event) {
            const form = event.target;
            const formData = new FormData(form);
            // 校核结果随表单提交：未校核时回退默认「现行」/ 空被替代编号
            formData.append('status', this.checkResult ? this.checkResult.status : '现行');
            formData.append('replaced_by_code', (this.checkResult && this.checkResult.replacedBy) || '');
            this.uploading = true;
            try {
                const resp = await fetch('/import/upload', { method: 'POST', body: formData });
                const html = await resp.text();
                const resultEl = document.getElementById('import-result');
                resultEl.innerHTML = html;
                // 让 htmx 扫描新插入的元素，启动轮询
                if (window.htmx) {
                    htmx.process(resultEl);
                }
            } catch (e) {
                document.getElementById('import-result').innerHTML = `<p style="color:red;">上传失败: ${e.message}</p>`;
                this.uploading = false;
            }
        },

        // 手动校核：调用 /import/validate-version，回填状态/被替代编号/AI 规范化建议
        async runVersionCheck() {
            const code = this.codeInput.trim();
            const title = this.titleInput.trim();
            if (!code || !title) { alert('请先填写规范编号与名称'); return; }
            this.checking = true;
            try {
                const resp = await fetch('/import/validate-version', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code, title }),
                });
                const data = await resp.json();
                const corrected = (data.corrected_code && data.corrected_code !== code)
                    || (data.corrected_title && data.corrected_title !== title)
                    ? { code: data.corrected_code, title: data.corrected_title } : null;
                this.checkResult = {
                    status: data.status, replacedBy: data.replaced_by_code || '',
                    ai_available: data.ai_available, corrected,
                };
                this.contentEdited = false;
            } catch (e) {
                alert('校核失败，请稍后重试');
            } finally {
                this.checking = false;
            }
        },

        // 应用 AI 规范化建议：回填编号/名称并同步 Alpine 状态
        applySuggestion() {
            if (!this.checkResult || !this.checkResult.corrected) return;
            document.querySelector('input[name=code]').value = this.checkResult.corrected.code;
            document.querySelector('input[name=title]').value = this.checkResult.corrected.title;
            this.codeInput = this.checkResult.corrected.code;
            this.titleInput = this.checkResult.corrected.title;
            this.checkResult.corrected = null;
            this.contentEdited = false;
        },
    }));
});
