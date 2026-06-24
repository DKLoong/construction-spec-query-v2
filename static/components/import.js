// 导入对话框组件
document.addEventListener('alpine:init', () => {
    Alpine.data('importDialog', () => ({
        open: false,
        uploading: false,

        openDialog() { this.open = true; },
        closeDialog() { this.open = false; this.uploading = false; },

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
