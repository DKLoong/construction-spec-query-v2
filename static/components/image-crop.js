/* 图片裁剪的**纯几何 + canvas 包装**（P4）。
 *
 * 与 `image-picker.js` 同一范式：能在无浏览器上下文里判定的部分（坐标换算）做成纯函数，
 * 供探针直接断言；只在真需要画布时才碰 DOM。
 *
 * ## 为什么需要「显示坐标 → 自然像素」这一步
 *
 * 用户在编辑器里拖的矩形是**显示坐标**（CSS 像素），而 `<canvas>` 的 `drawImage` 取的是
 * **自然像素**（`naturalWidth/Height`）。图在被 `max-width:100%` 缩放后两者不等
 * ⇒ 直接把显示矩形喂给 `drawImage` 会裁到错的地方，而且**画出来的图看着"有内容"**，
 * 很难发现错了。
 *
 * ## 两个必须做对的细节
 *
 * - **夹取**：源矩形越界时 `drawImage` **不报错**，它会取到边缘像素或画出空白 ⇒
 *   静默错位。故一律夹到 `[0, natW] × [0, natH]` 并取整。
 * - **最小尺寸**：显示上的一两像素在缩放后可能不足 1 自然像素 ⇒ 裁出一张 1×1 的图。
 *   与其产出垃圾，不如判否（返回 `null`）让调用方给提示。
 */
(function () {
    // 自然像素下的最小边长：小于它裁出来没有意义
    const MIN_SIZE = 4;

    /** 显示坐标 → **自然像素**坐标（本文件唯一的换算入口）。
     *
     *  不可用（图未加载完 / 区域太小）时返回 `null`，而不是 NaN 或一张垃圾图。 */
    function toNatural(rect, dispW, dispH, natW, natH) {
        if (!rect) return null;
        if (!(dispW > 0) || !(dispH > 0) || !(natW > 0) || !(natH > 0)) return null;
        const sx = natW / dispW, sy = natH / dispH;
        const clamp = function (v, hi) { return Math.max(0, Math.min(Math.round(v), hi)); };
        const x = clamp(rect.x * sx, natW);
        const y = clamp(rect.y * sy, natH);
        const x2 = clamp((rect.x + rect.w) * sx, natW);
        const y2 = clamp((rect.y + rect.h) * sy, natH);
        const w = x2 - x, h = y2 - y;
        if (w < MIN_SIZE || h < MIN_SIZE) return null;
        return { x: x, y: y, w: w, h: h };
    }

    /** 按自然像素矩形把图裁到**新画布**（不动原图、不写盘 —— 落盘由调用方走共用端点）。 */
    function drawCrop(img, r) {
        const c = document.createElement('canvas');
        c.width = r.w;
        c.height = r.h;
        c.getContext('2d').drawImage(img, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
        return c;
    }

    window.imageCrop = { MIN_SIZE: MIN_SIZE, toNatural: toNatural, drawCrop: drawCrop };
})();
