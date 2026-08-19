// 导入对话框组件
document.addEventListener('alpine:init', () => {
    Alpine.data('importDialog', () => ({
        open: false,
        uploading: false,

        openDialog() { this.open = true; },
        closeDialog() { this.open = false; this.uploading = false; },

        // 选择文件后自动识别规范编号/名称并回填（不匹配通用命名格式则跳过，交用户手动录入）
        async autoFillFromFilename(event) {
            const file = event.target.files && event.target.files[0];
            if (!file) return;
            const form = event.target.closest('form');
            const codeInput = form ? form.querySelector('input[name="code"]') : null;
            const titleInput = form ? form.querySelector('input[name="title"]') : null;
            if (!codeInput || !titleInput) return;
            // 已有手动输入则保留，不覆盖用户填写内容
            if (codeInput.value.trim() || titleInput.value.trim()) return;
            try {
                const fd = new FormData();
                fd.append('filename', file.name);
                const resp = await fetch('/import/parse-filename', { method: 'POST', body: fd });
                const data = await resp.json();
                if (data && data.matched) {
                    if (!codeInput.value.trim() && data.code) codeInput.value = data.code;
                    if (!titleInput.value.trim() && data.title) titleInput.value = data.title;
                }
            } catch (e) {
                // 识别接口异常静默忽略，不阻断用户手动录入
            }
        },

        async handleUpload(event) {
            const form = event.target;
            const formData = new FormData(form);
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
    }));
});
