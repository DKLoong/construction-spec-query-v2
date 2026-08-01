"""百度 AI Studio OCR API 客户端 — 支持多后端（PaddleOCR-VL / accurate_basic / 自定义）"""
import asyncio
import base64
import json
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
    """百度 AI Studio OCR API 客户端 — accurate_basic 同步接口"""

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
            "paragraph": "true",  # 修复：启用段落检测保留基本格式
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


# ═══════════════════════════════════════════
# PaddleOCR-VL 文档解析客户端
# ═══════════════════════════════════════════

class PaddleVLClient:
    """PaddleOCR 官网 V2 文档解析 API 客户端（异步：提交 → 轮询 → 下载 JSONL）

    接口：https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
    - multipart 上传 PDF + Authorization: bearer {token}
    - GET 轮询 /jobs/{jobId}，state 流转 pending/running/done/failed
    - done 后从 resultUrl.jsonUrl 下载 JSONL，逐页拼接 layoutParsingResults

    相比 accurate_basic，输出包含：
    - 结构化 Markdown（标题层级）
    - 表格（自动识别 Markdown 表格格式）
    - 公式（LaTeX 格式）
    - 图表识别
    - 阅读顺序保持
    """

    SUBMIT_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    MODEL = "PaddleOCR-VL-1.6"

    # 旧 snake_case 参数 → 官网 camelCase 参数映射（兼容旧版 ocr.paddle-vl.params 配置）
    _PARAM_MAP = {
        "analysis_chart": "useChartRecognition",
        "merge_tables": "mergeTables",
        "relevel_titles": "relevelTitles",
        "recognize_seal": "useSealRecognition",
    }

    # 官网 V2 API 推荐参数（与官方示例保持一致；ignoreLabels 忽略页眉页脚页码等噪声）
    DEFAULT_PARAMS = {
        "markdownIgnoreLabels": [
            "header", "header_image", "footer", "footer_image",
            "number", "footnote", "aside_text",
        ],
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useLayoutDetection": True,
        "useChartRecognition": False,
        "useSealRecognition": False,
        "useOcrForImageBlock": False,
        "mergeTables": True,
        "relevelTitles": True,
        "layoutShapeMode": "auto",
        "promptLabel": "ocr",
        "repetitionPenalty": 1,
        "temperature": 0,
        "topP": 1,
        "minPixels": 147384,
        "maxPixels": 2822400,
        "layoutNms": True,
        "restructurePages": True,
    }

    def __init__(self, access_token: str, params: dict | None = None):
        self.access_token = access_token
        self.params = self._normalize_params(params or {})

    def _normalize_params(self, params: dict) -> dict:
        """将旧 snake_case 参数名映射为官网 camelCase，未知参数原样透传"""
        return {self._PARAM_MAP.get(k, k): v for k, v in params.items()}

    def _optional_payload(self) -> dict:
        """合并默认参数与用户自定义参数"""
        payload = dict(self.DEFAULT_PARAMS)
        payload.update(self.params)
        return payload

    def _headers(self) -> dict:
        return {"Authorization": f"bearer {self.access_token}"}

    async def _submit_task(self, file_path: str,
                           retries: int = 3, retry_delay: float = 2.0) -> str:
        """multipart 上传 PDF，返回 jobId

        官网服务端偶发 HTTP 5xx（瞬时故障），对 5xx 做有限重试；
        4xx 属客户端错误（token 无效/文件问题），直接失败不重试。
        """
        import httpx

        file_name = Path(file_path).name
        optional = json.dumps(self._optional_payload())
        last_err: RuntimeError | None = None

        for attempt in range(1, retries + 1):
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f, "application/pdf")}
                data = {"model": self.MODEL, "optionalPayload": optional}
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        self.SUBMIT_URL,
                        headers=self._headers(),
                        data=data,
                        files=files,
                    )

            if resp.status_code == 200:
                body = resp.json()
                job_id = (body.get("data") or {}).get("jobId", "")
                if job_id:
                    return job_id
                # 200 但未返回 jobId：响应异常，视作可重试
                last_err = RuntimeError("OCR 任务提交失败: 未返回 jobId")
            elif resp.status_code < 500:
                # 4xx 客户端错误：重试无意义，直接失败
                logger.error("PaddleOCR-VL 任务提交失败 (HTTP %s): %s",
                             resp.status_code, resp.text[:500])
                raise RuntimeError(f"OCR 任务提交失败 (HTTP {resp.status_code})")
            else:
                # 5xx 服务端瞬时错误：重试
                last_err = RuntimeError(f"OCR 任务提交失败 (HTTP {resp.status_code})")
                logger.warning(
                    "PaddleOCR-VL 任务提交 5xx (HTTP %s)，第 %d/%d 次，稍后重试: %s",
                    resp.status_code, attempt, retries, resp.text[:300],
                )
                if attempt < retries:
                    await asyncio.sleep(retry_delay * attempt)

        assert last_err is not None
        raise last_err

    async def test_connectivity(self) -> str:
        """验证 API 连通性：提交一个最小 PDF 任务，返回 jobId

        官网 V2 API 无独立连通性端点，以能否提交任务并拿到 jobId 作为
        连通判定。token 无效时官网返回 401，由 _submit_task 抛 RuntimeError。
        """
        import os
        import tempfile

        import fitz

        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "PaddleOCR-VL connectivity test")
            doc.save(tmp_path)
            doc.close()
            return await self._submit_task(tmp_path)
        finally:
            os.unlink(tmp_path)

    async def _poll_result(self, job_id: str, max_wait: int = 600,
                           interval: int = 5) -> dict:
        """GET 轮询直到 state=done/failed 或超时，返回 data dict"""
        import httpx

        url = f"{self.SUBMIT_URL}/{job_id}"
        waited = 0
        while waited < max_wait:
            await asyncio.sleep(interval)
            waited += interval

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                body = resp.json()

            data = body.get("data") or {}
            state = data.get("state", "")

            if state == "done":
                logger.info("PaddleOCR-VL 任务完成 (job_id=%s, 耗时 %ds)", job_id, waited)
                return data
            if state == "failed":
                raise RuntimeError(f"OCR 任务失败: {data.get('errorMsg', '未知错误')}")

            logger.debug("PaddleOCR-VL 轮询中 (job_id=%s, 已等待 %ds, 状态=%s)",
                         job_id, waited, state)

        raise TimeoutError(f"OCR 任务超时 (已等待 {max_wait}s, job_id={job_id})")

    async def _download_markdown(self, url: str) -> str:
        """从 jsonUrl 下载 JSONL，逐页拼接 markdown 文本"""
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        parts = []
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            for lp in record.get("result", {}).get("layoutParsingResults", []):
                md = lp.get("markdown", {})
                if md.get("text"):
                    parts.append(md["text"])
        return "\n".join(parts)

    def ocr_pdf_to_md(self, pdf_path: str, output_dir: str | None = None) -> str:
        """PDF 文档解析 → Markdown（同步封装，供后台任务调用）

        与 PaddleStudioAPI.ocr_pdf_to_md 保持相同签名以便工厂函数互换。
        """
        return asyncio.run(self._ocr_pdf_to_md_async(pdf_path, output_dir))

    async def _ocr_pdf_to_md_async(self, pdf_path: str,
                                   output_dir: str | None = None) -> str:
        """异步 PDF 文档解析 → 下载 JSONL → 拼接保存 Markdown"""
        from app.config import OUTPUT_DIR

        if output_dir is None:
            output_dir = str(Path(OUTPUT_DIR) / Path(pdf_path).stem)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 提交任务
        job_id = await self._submit_task(pdf_path)
        logger.info("PaddleOCR-VL 任务已提交: job_id=%s, 文件=%s",
                    job_id, Path(pdf_path).name)

        # 轮询结果
        result = await self._poll_result(job_id)

        # 下载 JSONL → 拼接 Markdown
        json_url = (result.get("resultUrl") or {}).get("jsonUrl", "")
        if not json_url:
            raise RuntimeError("OCR 结果中缺少 resultUrl.jsonUrl")

        md_text = await self._download_markdown(json_url)

        md_path = str(Path(output_dir) / f"{Path(pdf_path).stem}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_text)

        logger.info("PaddleOCR-VL Markdown 已保存: %s (%d 字符)", md_path, len(md_text))
        return md_path


# ═══════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════

def create_ocr_client() -> object:
    """根据 settings 中的 ocr.backend 配置创建 OCR 客户端。

    返回实现了 ocr_pdf_to_md(pdf_path, output_dir=None) -> str 的客户端实例。

    支持的后端:
        paddle-vl   — PaddleOCR-VL 文档解析（推荐，结构化 Markdown）
        accurate-basic — 通用文字识别（旧版兼容，纯文本）
        custom      — 自定义 OCR 端点

    Raises:
        RuntimeError: OCR 令牌未配置或后端未知
    """
    from app.ocr.provider_presets import OCR_PROVIDERS

    backend = get_setting("ocr.backend") or "paddle-vl"

    # 向后兼容：新 key (ocr.{backend}.access_token) 未配置时回退到 ocr.access_token
    token = (get_setting(f"ocr.{backend}.access_token")
             or get_setting("ocr.access_token"))

    if not token:
        raise RuntimeError(
            "OCR 令牌未配置，请在设置页面选择 OCR 后端并配置 access_token"
        )

    if backend == "accurate-basic":
        return PaddleStudioAPI(access_token=token)

    if backend == "custom":
        preset = OCR_PROVIDERS.get("custom", {})
        base_url = get_setting("ocr.custom.base_url") or preset.get("url", "")
        model = get_setting("ocr.custom.model") or ""
        params_str = get_setting("ocr.custom.params") or "{}"
        try:
            extra_params = json.loads(params_str)
        except json.JSONDecodeError:
            extra_params = {}

        # 自定义后端使用通用 OCR 客户端
        return CustomOCRClient(
            access_token=token,
            base_url=base_url,
            model=model,
            extra_params=extra_params,
        )

    # 默认使用 paddle-vl
    provider = OCR_PROVIDERS.get(backend, OCR_PROVIDERS["paddle-vl"])
    params_str = get_setting(f"ocr.{backend}.params") or ""
    if params_str:
        try:
            params = json.loads(params_str)
        except json.JSONDecodeError:
            params = dict(provider.get("default_params", {}))
    else:
        params = dict(provider.get("default_params", {}))

    return PaddleVLClient(access_token=token, params=params)


class CustomOCRClient:
    """自定义 OCR 客户端 — 支持任意 OpenAI Vision 兼容 API"""

    def __init__(self, access_token: str = "", base_url: str = "",
                 model: str = "", extra_params: dict | None = None):
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.extra_params = extra_params or {}

    def ocr_pdf_to_md(self, pdf_path: str, output_dir: str | None = None) -> str:
        """PDF → 逐页渲染 → 自定义 API → Markdown"""
        import fitz
        from app.config import OUTPUT_DIR

        if output_dir is None:
            output_dir = str(Path(OUTPUT_DIR) / Path(pdf_path).stem)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        md_path = str(Path(output_dir) / f"{Path(pdf_path).stem}.md")
        doc = fitz.open(pdf_path)
        try:
            md_parts = []
            for i in range(len(doc)):
                page = doc[i]
                pix = page.get_pixmap(dpi=200)
                img_path = str(Path(output_dir) / f"page_{i + 1:04d}.png")
                pix.save(img_path)
                try:
                    text = asyncio.run(self._ocr_page_async(img_path))
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

    async def _ocr_page_async(self, img_path: str) -> str:
        """调用自定义 API 识别单页图片"""
        import httpx

        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": "请识别图片中的文字，输出为 Markdown 格式，保留表格和标题结构。"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
            "max_tokens": 4096,
            **self.extra_params,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return ""
