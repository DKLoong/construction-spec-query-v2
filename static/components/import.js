// 导入对话框组件
document.addEventListener('alpine:init', () => {
    Alpine.data('importDialog', () => ({
        open: false,
        uploading: false,
        // 记录输入框当前值是否来自自动识别（区分自动填充 vs 用户手动输入）
        autoFilled: { code: false, title: false },

        openDialog() { this.open = true; },
        closeDialog() { this.open = false; this.uploading = false; },

        // 用户手动编辑输入框时清除自动填充标记：后续选文件不再覆盖手动输入
        markManual(field) {
            this.autoFilled[field] = false;
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
                    }
                    if (wantTitle && data.title) {
                        titleInput.value = data.title;
                        this.autoFilled.title = true;
                    }
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
