"""模板结构回归测试：防止已修复的前端问题被回退

这些是静态断言，验证两个历史 bug 修复后的模板结构不再退化：
1. 导入表单「强制 OCR」复选框不得重新引入 width:auto
   （width:auto 使 Pico appearance:none checkbox 计算宽度仅 4px，
    导致 Chrome 合成器在取消选中时 :checked 背景图不重绘）
2. 设置弹窗 OCR 标签页必须保留「测试连通性」按钮与结果提示
   （OCR 多后端重构时曾丢失，按钮/JS/后端路由均存在但 HTML 缺失）
"""
from pathlib import Path

PARTIALS = Path(__file__).resolve().parent.parent / "app" / "templates" / "partials"


def _read(name: str) -> str:
    return (PARTIALS / name).read_text(encoding="utf-8")


def test_force_ocr_checkbox_uses_pico_default_width():
    """强制OCR复选框不得带 width:auto（否则宽度塌缩为4px导致视觉不刷新）"""
    html = _read("tree_panel.html")
    line = next(l for l in html.splitlines() if 'name="force_ocr"' in l)
    assert "width:auto" not in line, "force_ocr 复选框不应使用 width:auto"
    assert 'type="checkbox"' in line
    assert 'value="true"' in line


def test_settings_ocr_tab_has_test_connection_button():
    """OCR 标签页必须含测试连通性按钮与结果提示（在 AI 标签页之前）"""
    html = _read("settings_dialog.html")
    ocr_part = html.split("<!-- AI 标签页 -->")[0]
    assert "testOCR()" in ocr_part, "OCR 标签页缺少 testOCR() 调用"
    assert "ocrTestResult" in ocr_part, "OCR 标签页缺少 ocrTestResult 结果绑定"
    assert "currentOCRToken" in ocr_part


def test_ocr_review_rewrite_keeps_imgs_segment():
    """审查页图片路径改写必须保留 imgs/ 子路径段

    曾误把 `src="imgs/xxx.jpg"` 的 imgs/ 前缀整个替换掉，导致请求
    `/import/review/{task_id}/img_in_*.jpg`（缺 imgs/ 段）→ 路由 404 → 破图图标。
    """
    html = _read("ocr_review.html")
    assert "_rewriteImgPaths" in html, "审查页缺少图片路径改写函数"
    # HTML <img src="imgs/..."> 改写时，base 后必须补回 imgs/，不能把 imgs/ 段替换掉
    assert "${base}imgs/" in html, "HTML 图片改写应保留 imgs/ 子路径"
    # markdown 语法 ![..](imgs/..) 同样保留 imgs/
    assert "(${base}imgs/" in html, "markdown 图片改写应保留 imgs/ 子路径"
