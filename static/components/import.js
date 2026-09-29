// 导入对话框组件
document.addEventListener('alpine:init', () => {
    Alpine.data('importDialog', () => ({
        open: false,
        uploading: false,
        // 是否已选择文件：未选时「开始导入」按钮被白色蒙版遮罩并禁用（见 tree_panel.html）
        hasFile: false,
        // 校核相关状态：codeInput/titleInput 为当前输入框内容，checkResult 为校核结果
        codeInput: '',
        titleInput: '',
        checking: false,
        checkResult: null,   // {status, replacedBy, ai_available, corrected: {code,title}|null}
        contentEdited: false,

        // 打开对话框时重置校核状态，避免上次导入的 checkResult/contentEdited 残留
        // 污染本次 handleUpload（携带上一轮陈旧 status/replacedBy 错误标废旧规范）
        init() {
            // 导入进入终态（done/review_needed/error）时由 import_progress.html 广播
            // import-finished 事件，这里复位 uploading，允许再次导入
            window.addEventListener('import-finished', () => {
                this.uploading = false;
            });
        },
        openDialog() {
            this.open = true;
            this.checkResult = null;
            this.contentEdited = false;
        },
        // 关闭弹窗。导入进行中先确认：任务跑在服务端后台（与浏览器无关），关掉不会中断，
        // 但进度就看不见了 —— 误触会让人以为任务丢了。
        // 注意**不复位 uploading**：复位会让「开始导入」提前解禁（文件仍选着）→ 重复导入。
        // uploading 只走三条终态路径复位：import-finished 事件、非轮询终态响应、请求异常。
        closeDialog() {
            if (this.uploading && !window.confirm(
                '导入正在后台进行，关闭弹窗不会中断任务，可再次打开本弹窗查看进度。确定关闭？'
            )) return;
            this.open = false;
        },

        // 用户手动编辑输入框：同步 Alpine 状态，并作废上一轮校核结论。
        // 为什么要作废：checkResult.replacedBy 会随表单无条件提交，而编号/名称已不是校核时
        // 的那一个 → 服务端 _link_replacement 在 status=现行 时会把库里该编号的规范静默标废。
        // 「校核结论绑定编号+名称，任一变化即失效」是唯一自洽的口径。
        markManual(field) {
            if (field === 'code') this.codeInput = document.querySelector('input[name=code]').value;
            if (field === 'title') this.titleInput = document.querySelector('input[name=title]').value;
            this.checkResult = null;
            this.contentEdited = true;  // 提示重新校核（不自动触发）
        },

        // 选择文件后按文件名**整体改写**规范编号/名称；不匹配识别规则则清空，交用户手动录入。
        // 「一律改写」而非「只填空缺」是用户裁定：文件即规范身份 —— 换了个文件还留着上一个
        // 文件的编号，配上此时已解禁的「开始导入」，就是拿着错编号导入。手动输入的内容同样
        // 被覆盖（该行为在文件选择框的 tooltip 里对用户声明）。
        async autoFillFromFilename(event) {
            const file = event.target.files && event.target.files[0];
            // 同步「已选文件」状态：未选/取消选择 → 恢复蒙版与禁用
            this.hasFile = !!file;
            if (!file) return;
            const form = event.target.closest('form');
            const codeInput = form ? form.querySelector('input[name="code"]') : null;
            const titleInput = form ? form.querySelector('input[name="title"]') : null;
            if (!codeInput || !titleInput) return;
            let code = '';
            let title = '';
            try {
                const fd = new FormData();
                fd.append('filename', file.name);
                const resp = await fetch('/import/parse-filename', { method: 'POST', body: fd });
                const data = await resp.json();
                if (data && data.matched) {
                    code = data.code || '';
                    title = data.title || '';
                }
            } catch (e) {
                // 接口异常/非 JSON 响应（如 401 跳登录页）也走清空：文件已换，
                // 留着上一个文件的值比留空更危险，故不保留旧值
            }
            codeInput.value = code;
            titleInput.value = title;
            // 同步 Alpine 状态：x-model 只做初始绑定，直接改 DOM value 不会回写状态，
            // 不同步的话 runVersionCheck 读到的还是旧值
            this.codeInput = code;
            this.titleInput = title;
            // 编号/名称已变 → 上一轮校核结论（尤其 replacedBy）不再适用，必须作废
            this.checkResult = null;
            this.contentEdited = false;
        },

        async handleUpload(event) {
            const form = event.target;
            // 前端前置校验：未选择文件 / 扩展名不支持 → 直接提示，不发导入请求
            //（避免后端兜底报错文案与「未选文件」混淆；合法扩展名与 <input accept=".md,.pdf"> 一致）
            const fileInput = form.querySelector('input[name="file"]');
            const file = fileInput && fileInput.files && fileInput.files[0];
            const resultBox = document.getElementById('import-result');
            if (!file) {
                if (resultBox) {
                    resultBox.innerHTML = '<p style="color:var(--pico-muted-color);">未选择文件，不可导入</p>';
                }
                return;
            }
            const dotIdx = file.name.lastIndexOf('.');
            const ext = dotIdx >= 0 ? file.name.slice(dotIdx + 1).toLowerCase() : '';
            if (!['md', 'pdf'].includes(ext)) {
                if (resultBox) {
                    resultBox.innerHTML =
                        `<p style="color:#c00;">导入失败：仅支持 .md/.pdf（当前为 ${ext ? '.' + ext : '无扩展名'}）</p>`;
                }
                return;
            }
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
                // 轮询态（上传成功进入后台处理）由 import-finished 事件复位 uploading；
                // 非轮询响应（重复导入/即时错误，返回最终态 HTML）直接复位，允许再次上传
                if (!html.includes('hx-get="/import/progress/')) {
                    this.uploading = false;
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
