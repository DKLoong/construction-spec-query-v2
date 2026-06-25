// 搜索组件
document.addEventListener('alpine:init', () => {
    Alpine.data('searchBox', () => ({
        keyword: '',
        loading: false,

        async search() {
            this.loading = true;
            const params = new URLSearchParams();
            if (this.keyword) params.append('keyword', this.keyword);
            try {
                const resp = await fetch(`/search?${params.toString()}`);
                const html = await resp.text();
                const target = document.querySelector('.center-panel');
                if (target) target.innerHTML = html;
            } catch (e) {
                console.error('搜索失败:', e);
            } finally {
                this.loading = false;
            }
        },
    }));
});
