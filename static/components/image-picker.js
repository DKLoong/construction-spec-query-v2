/* 左栏「拖框取图」的**纯几何层**（P4 第一刀 · 原型）。
 *
 * 本文件只做**纯函数级**的事（给定矩形与坐标 → 新矩形 / 判定 / 换算），不碰 Alpine 状态、
 * 不碰 DOM。由 `review-blocks.js` 在取图模式里调用（与 `md-inline.js` / `table-editor.js`
 * 同一模式：纯逻辑一个文件，状态与事件留在组件里）。
 *
 * ## 坐标系（本文件存在的理由）
 *
 * 选框量出来的是**画布内的 CSS 像素**，而裁切端点
 * `GET /import/review/{task_id}/crop/{page}.png` 的 `x/y/w/h` 用的是 OCR **页面栅格**坐标
 * （`layout.pages[].width/height`，与版块 `bbox` 同一系，见 `app/routes/import_routes.py`
 * 的 `review_block_crop`：它内部再除以 `base = 栅格宽 / PDF 点宽` 换成 PDF 点）。
 *
 * `.rvb-canvas` 的 CSS 宽 ≡ `页栅格宽 × zoom`（见 `review-blocks.js` 的 `canvasStyle()`）
 * ⇒ 换算就是「按画布 CSS 宽归一化，再乘栅格宽」，**与 zoom、devicePixelRatio 都无关**
 * （与 `boxStyle()` 的百分比定位互为反函数）。
 *
 * ⚠️ **坐标换算只有 `toGrid` 这一个入口** —— 不要在组件里另写一份。两份账必然分叉，
 *    而不一致的表现是「框选看着对、裁出来的图偏了」，很难往回查。
 */
(function () {
    // 小于这个边长（CSS px）视为**碎拖**：不给 crop 端点送 1×1 的请求
    const MIN_SIZE = 4;

    // 手柄命中容差（CSS px）。判据是 ±tol 的**方框**（不是欧氏圆），
    // 且**角优先于边、边优先于 move** —— 否则贴着角拖会退化成拉边。
    const HANDLE_TOL = 6;

    /** 两个对角点 → 正矩形。反向拖（右下→左上）也得到同样的结果。 */
    function normalize(x0, y0, x1, y1) {
        return {
            x: Math.min(x0, x1), y: Math.min(y0, y1),
            w: Math.abs(x1 - x0), h: Math.abs(y1 - y0),
        };
    }

    /** 夹到页内。完全出界得到**零尺寸**（而不是负宽高）⇒ 调用方据此丢弃。 */
    function clampRect(r, pageW, pageH) {
        if (!r) return null;
        const x = Math.max(0, Math.min(r.x, pageW));
        const y = Math.max(0, Math.min(r.y, pageH));
        const x2 = Math.max(0, Math.min(r.x + r.w, pageW));
        const y2 = Math.max(0, Math.min(r.y + r.h, pageH));
        return { x: x, y: y, w: Math.max(0, x2 - x), h: Math.max(0, y2 - y) };
    }

    /** 鼠标落在哪：8 个手柄之一 / `'move'`（框内）/ `null`（没命中）。 */
    function hitHandle(r, px, py, tol) {
        if (!r) return null;
        const t = (tol === undefined) ? HANDLE_TOL : tol;
        const x0 = r.x, y0 = r.y, x1 = r.x + r.w, y1 = r.y + r.h;
        const near = function (v, target) { return Math.abs(v - target) <= t; };
        if (px < x0 - t || px > x1 + t || py < y0 - t || py > y1 + t) return null;
        // 角优先（否则贴角拖会退化成拉边）
        if (near(px, x0) && near(py, y0)) return 'nw';
        if (near(px, x1) && near(py, y0)) return 'ne';
        if (near(px, x1) && near(py, y1)) return 'se';
        if (near(px, x0) && near(py, y1)) return 'sw';
        // 边
        if (near(py, y0)) return 'n';
        if (near(px, x1)) return 'e';
        if (near(py, y1)) return 's';
        if (near(px, x0)) return 'w';
        return 'move';
    }

    /** 按手柄把被拖的那条边移到 `(px, py)`；未涉及的边一律不动。
     *
     * ⚠️ `'move'` **必须特判**：`'move'.indexOf('e') >= 0` 为真，不特判会被当成「拉右缘」，
     *    表现为整体拖框时右缘乱跑。整体平移由组件按起点差量处理，不走这里。
     */
    function resize(r, handle, px, py) {
        if (!r) return null;
        if (handle === 'move') return { x: r.x, y: r.y, w: r.w, h: r.h };
        let x0 = r.x, y0 = r.y, x1 = r.x + r.w, y1 = r.y + r.h;
        if (handle.indexOf('w') >= 0) x0 = px;
        if (handle.indexOf('e') >= 0) x1 = px;
        if (handle.indexOf('n') >= 0) y0 = py;
        if (handle.indexOf('s') >= 0) y1 = py;
        return normalize(x0, y0, x1, y1);
    }

    /** **唯一的坐标换算入口**：画布 CSS 像素 → OCR 页面栅格。
     *
     * 任一尺寸非正（页图还没加载出来）时返回 `null`，而不是 NaN / 除零。 */
    function toGrid(r, elW, elH, gridW, gridH) {
        if (!r) return null;
        if (!(elW > 0) || !(elH > 0) || !(gridW > 0) || !(gridH > 0)) return null;
        const sx = gridW / elW, sy = gridH / elH;
        return { x: r.x * sx, y: r.y * sy, w: r.w * sx, h: r.h * sy };
    }

    /** 够不够大（碎拖 / 空框判否）。 */
    function isUsable(r) {
        return !!(r && r.w >= MIN_SIZE && r.h >= MIN_SIZE);
    }

    window.imagePicker = { MIN_SIZE: MIN_SIZE, HANDLE_TOL: HANDLE_TOL,
                           normalize: normalize, clampRect: clampRect,
                           hitHandle: hitHandle, resize: resize,
                           toGrid: toGrid, isUsable: isUsable };
})();
