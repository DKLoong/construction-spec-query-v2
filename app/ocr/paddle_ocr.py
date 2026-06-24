import inspect
import logging
from pathlib import Path
from app.config import OUTPUT_DIR

logger = logging.getLogger(__name__)


def _get_ocr():
    """懒加载 PaddleOCR，处理 2.x/3.x API 兼容"""
    try:
        from paddleocr import PaddleOCR
        sig = inspect.signature(PaddleOCR.__init__)

        if "lang" in sig.parameters and len(sig.parameters) <= 3:
            # PaddleOCR 3.x
            return PaddleOCR(lang="ch")
        else:
            # PaddleOCR 2.x
            return PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    except ImportError:
        logger.warning("PaddleOCR 未安装，OCR 功能不可用")
        return None


def ocr_image(img_path: str) -> str:
    """对单张图片执行 OCR，返回识别文本"""
    ocr = _get_ocr()
    if ocr is None:
        raise RuntimeError("PaddleOCR 未安装")

    sig = inspect.signature(ocr.ocr)
    if "img" in sig.parameters or len(sig.parameters) >= 1:
        result = ocr.ocr(img_path)
        lines = []
        if result and isinstance(result, list):
            for item in result[0] if result else []:
                if len(item) >= 2:
                    lines.append(item[1][0] if isinstance(item[1], (list, tuple)) else str(item[1]))
        return "\n".join(lines)
    else:
        result = ocr.predict(img_path)
        lines = []
        for item in result if result else []:
            text = item.get("rec_text", "") if isinstance(item, dict) else str(item)
            lines.append(text)
        return "\n".join(lines)


def ocr_pdf_to_md(pdf_path: str, output_dir: str | None = None) -> str:
    """对扫描件 PDF 逐页 OCR，输出 Markdown"""
    import fitz
    if output_dir is None:
        output_dir = str(Path(OUTPUT_DIR) / Path(pdf_path).stem)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    md_path = str(Path(output_dir) / f"{Path(pdf_path).stem}.md")
    doc = fitz.open(pdf_path)
    md_parts = []

    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=200)
        img_path = str(Path(output_dir) / f"page_{i:04d}.png")
        pix.save(img_path)
        try:
            text = ocr_image(img_path)
            md_parts.append(f"## 第{i}页\n\n{text}\n")
        except Exception as e:
            md_parts.append(f"## 第{i}页\n\n_[OCR 失败: {e}]_\n")
        finally:
            Path(img_path).unlink(missing_ok=True)

    doc.close()

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_parts))

    return md_path
