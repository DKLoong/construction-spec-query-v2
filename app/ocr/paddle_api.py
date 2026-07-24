"""百度 AI Studio OCR API 客户端"""
import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 每批最大页数
_BATCH_PAGES = 99


def get_setting(key: str) -> str:
    """从 settings 表读取配置值"""
    from app.database import get_db
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else ""
    except Exception as e:
        logger.warning("get_setting('%s') 读取失败: %s", key, e)
        return ""


class PaddleStudioAPI:
    """百度 AI Studio OCR API 客户端"""

    BASE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1"

    def __init__(self, access_token: str):
        self.access_token = access_token

    async def _ocr_image_async(self, img_path: str) -> str:
        """对单张图片执行 OCR（accurate_basic），返回识别文本"""
        import httpx

        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        url = f"{self.BASE_URL}/accurate_basic"
        params = {"access_token": self.access_token}
        payload = {
            "image": img_b64,
            "language_type": "CHN_ENG",
            "detect_direction": "true",
            "paragraph": "false",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, params=params, data=payload)
            resp.raise_for_status()
            data = resp.json()

        if "error_code" in data:
            error_msg = data.get("error_msg", "未知错误")
            logger.error("OCR API 错误 (code=%s): %s", data["error_code"], error_msg)
            raise RuntimeError(f"OCR API 返回错误: {error_msg}")

        words_result = data.get("words_result", [])
        lines = [item.get("words", "") for item in words_result]
        return "\n".join(lines)

    def ocr_image(self, img_path: str) -> str:
        """同步封装（供后台任务调用）"""
        import asyncio
        return asyncio.run(self._ocr_image_async(img_path))

    def ocr_pdf_to_md(self, pdf_path: str, output_dir: str | None = None) -> str:
        """PDF 逐页渲染 PNG → 分批调 OCR API → 拼 Markdown

        每批 _BATCH_PAGES 页，分批处理避免超时，完成后合并为完整结果。
        """
        import fitz
        from app.config import OUTPUT_DIR

        if output_dir is None:
            output_dir = str(Path(OUTPUT_DIR) / Path(pdf_path).stem)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        md_path = str(Path(output_dir) / f"{Path(pdf_path).stem}.md")
        doc = fitz.open(pdf_path)
        try:
            total_pages = len(doc)
            total_batches = (total_pages + _BATCH_PAGES - 1) // _BATCH_PAGES

            if total_batches > 1:
                logger.info(
                    "PDF 共 %d 页，将分 %d 批处理（每批 %d 页）",
                    total_pages, total_batches, _BATCH_PAGES,
                )

            md_parts = []
            for batch_idx in range(total_batches):
                start_page = batch_idx * _BATCH_PAGES
                end_page = min(start_page + _BATCH_PAGES, total_pages)

                if total_batches > 1:
                    logger.info(
                        "OCR 批次 %d/%d: 第 %d-%d 页",
                        batch_idx + 1, total_batches, start_page + 1, end_page,
                    )

                for i in range(start_page, end_page):
                    page = doc[i]
                    pix = page.get_pixmap(dpi=200)
                    img_path = str(Path(output_dir) / f"page_{i + 1:04d}.png")
                    pix.save(img_path)
                    try:
                        text = self.ocr_image(img_path)
                        md_parts.append(f"## 第{i + 1}页\n\n{text}\n")
                    except Exception as e:
                        md_parts.append(f"## 第{i + 1}页\n\n_[OCR 失败: {e}]_\n")
                    finally:
                        Path(img_path).unlink(missing_ok=True)
        finally:
            doc.close()

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_parts))

        return md_path
