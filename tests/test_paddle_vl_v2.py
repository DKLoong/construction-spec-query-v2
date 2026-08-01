"""PaddleVLClient v2 — PaddleOCR 官网 V2 API 客户端测试

基于用户官方代码（paddleocr.aistudio-app.com/api/v2/ocr/jobs）重写：
- multipart 上传 + Authorization: bearer {token}
- GET 轮询 /jobs/{jobId}
- 结果从 resultUrl.jsonUrl 下载 JSONL，逐页拼接 layoutParsingResults
"""
import asyncio
import json

import httpx
import pytest

from app.ocr.paddle_api import PaddleVLClient

TOKEN = "test-token"


class MockResponse:
    """httpx 响应模拟：json() / text / content / raise_for_status()"""

    def __init__(self, payload=None, text="", status_code=200, content=None):
        self._payload = payload if payload is not None else {}
        self._text = text
        self.status_code = status_code
        # content 未显式指定时，从 text 派生（兼容图片二进制场景需显式传入）
        self.content = content if content is not None else (
            self._text.encode() if isinstance(self._text, str) else self._text
        )

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text

    def raise_for_status(self):
        pass


def _new_client(params=None):
    return PaddleVLClient(access_token=TOKEN, params=params or {})


# ═══════════════════════════════════════════════
# _submit_task：multipart 上传 → jobId
# ═══════════════════════════════════════════════

def test_submit_task_returns_jobid_and_uses_bearer_auth(monkeypatch, tmp_path):
    """提交任务：multipart 上传、Authorization bearer、解析 jobId"""
    captured = {}

    async def mock_post(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["data"] = kwargs.get("data")
        captured["files"] = kwargs.get("files")
        return MockResponse({"data": {"jobId": "job-123"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    job_id = asyncio.run(_new_client()._submit_task(str(pdf)))

    assert job_id == "job-123"
    assert captured["url"] == PaddleVLClient.SUBMIT_URL
    assert captured["headers"]["Authorization"] == f"bearer {TOKEN}"
    # multipart 文件
    assert captured["files"] is not None
    # model 与 optionalPayload(JSON 字符串)
    assert captured["data"]["model"] == "PaddleOCR-VL-1.6"
    payload = json.loads(captured["data"]["optionalPayload"])
    assert "markdownIgnoreLabels" in payload


def test_submit_task_raises_when_missing_jobid(monkeypatch, tmp_path):
    """提交成功但无 jobId → RuntimeError"""
    async def mock_post(self, url, **kwargs):
        return MockResponse({"data": {}})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    with pytest.raises(RuntimeError, match="jobId"):
        asyncio.run(_new_client()._submit_task(str(pdf)))


def test_submit_task_raises_on_http_error(monkeypatch, tmp_path):
    """HTTP 非 200（认证失败）→ RuntimeError"""
    async def mock_post(self, url, **kwargs):
        return MockResponse({"errorMsg": "invalid token"}, status_code=401)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    with pytest.raises(RuntimeError):
        asyncio.run(_new_client()._submit_task(str(pdf)))


# ═══════════════════════════════════════════════
# _poll_result：GET 轮询 state 流转
# ═══════════════════════════════════════════════

def test_poll_returns_result_when_done(monkeypatch):
    """轮询到 state=done → 返回含 resultUrl 的 data"""
    states = iter(["running", "running", "done"])
    urls = []

    async def mock_get(self, url, **kwargs):
        urls.append(url)
        state = next(states)
        if state == "done":
            return MockResponse({"data": {
                "state": "done",
                "resultUrl": {"jsonUrl": "http://x/jsonl"},
            }})
        return MockResponse({"data": {"state": state}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = asyncio.run(_new_client()._poll_result("job-123", max_wait=60, interval=1))
    assert result["resultUrl"]["jsonUrl"] == "http://x/jsonl"
    # 轮询 URL 含 jobId
    assert any("job-123" in u for u in urls)


def test_poll_raises_on_failed(monkeypatch):
    """state=failed → RuntimeError 带 errorMsg"""
    async def mock_get(self, url, **kwargs):
        return MockResponse({"data": {"state": "failed", "errorMsg": "parse error"}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(RuntimeError, match="parse error"):
        asyncio.run(_new_client()._poll_result("job-123", max_wait=10, interval=1))


def test_poll_raises_on_timeout(monkeypatch):
    """超时未 done → TimeoutError"""
    async def mock_get(self, url, **kwargs):
        return MockResponse({"data": {"state": "pending"}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(TimeoutError):
        asyncio.run(_new_client()._poll_result("job-123", max_wait=3, interval=1))


# ═══════════════════════════════════════════════
# _download_markdown：JSONL 逐页拼接
# ═══════════════════════════════════════════════

def test_download_joins_jsonl_pages(monkeypatch):
    """JSONL 每行 layoutParsingResults[].markdown.text → 拼接"""
    jsonl = "\n".join([
        json.dumps({"result": {"layoutParsingResults": [
            {"markdown": {"text": "### 页1标题\n\n正文1"}},
        ]}}),
        json.dumps({"result": {"layoutParsingResults": [
            {"markdown": {"text": "## 页2标题\n\n正文2"}},
        ]}}),
    ])

    async def mock_get(self, url, **kwargs):
        return MockResponse(text=jsonl)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    md = asyncio.run(_new_client()._download_markdown("http://x/jsonl"))
    assert "### 页1标题" in md
    assert "正文1" in md
    assert "## 页2标题" in md
    assert "正文2" in md


def test_download_handles_empty_jsonl(monkeypatch):
    """空 JSONL → 空字符串，不报错"""
    async def mock_get(self, url, **kwargs):
        return MockResponse(text="")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    md = asyncio.run(_new_client()._download_markdown("http://x/jsonl"))
    assert md == ""


def test_download_saves_markdown_images(monkeypatch, tmp_path):
    """markdown.images 中的图片下载到 output_dir/相对路径（URL 与 data URL 两种）"""
    import base64

    b64 = base64.b64encode(b"fakejpeg").decode()
    jsonl = "\n".join([
        json.dumps({"result": {"layoutParsingResults": [
            {"markdown": {
                "text": "### 页1\n\n正文1\n",
                "images": {
                    "imgs/img_in_image_box_a.jpg": "http://img/a.jpg",
                    "imgs/img_in_image_box_b.jpg": f"data:image/jpeg;base64,{b64}",
                },
            }},
        ]}}),
    ])

    async def mock_get(self, url, **kwargs):
        if url == "http://x/jsonl":
            return MockResponse(text=jsonl)
        return MockResponse(content=b"image-bytes")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    md = asyncio.run(_new_client()._download_markdown("http://x/jsonl", str(tmp_path)))

    assert "### 页1" in md
    assert "正文1" in md
    # URL 形式图片下载到对应相对路径
    assert (tmp_path / "imgs" / "img_in_image_box_a.jpg").read_bytes() == b"image-bytes"
    # data URL 形式图片 base64 解码落盘
    assert (tmp_path / "imgs" / "img_in_image_box_b.jpg").read_bytes() == b"fakejpeg"


def test_download_skips_failed_images(monkeypatch, tmp_path):
    """图片下载失败只记日志，不影响 text 拼接与后续图片"""
    jsonl = "\n".join([
        json.dumps({"result": {"layoutParsingResults": [
            {"markdown": {
                "text": "正文",
                "images": {
                    "imgs/bad.jpg": "http://img/bad.jpg",
                    "imgs/good.jpg": "http://img/good.jpg",
                },
            }},
        ]}}),
    ])

    async def mock_get(self, url, **kwargs):
        if url == "http://x/jsonl":
            return MockResponse(text=jsonl)
        if url == "http://img/good.jpg":
            return MockResponse(content=b"good")
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    md = asyncio.run(_new_client()._download_markdown("http://x/jsonl", str(tmp_path)))

    assert md == "正文"
    assert not (tmp_path / "imgs" / "bad.jpg").exists()
    assert (tmp_path / "imgs" / "good.jpg").read_bytes() == b"good"


def test_download_rejects_path_traversal_images(monkeypatch, tmp_path):
    """恶意相对路径（含 .. 或绝对路径）不被写入，防路径穿越"""
    jsonl = "\n".join([
        json.dumps({"result": {"layoutParsingResults": [
            {"markdown": {
                "text": "正文",
                "images": {
                    "../evil.jpg": "http://img/evil.jpg",
                    "/abs/evil2.jpg": "http://img/evil2.jpg",
                },
            }},
        ]}}),
    ])

    async def mock_get(self, url, **kwargs):
        if url == "http://x/jsonl":
            return MockResponse(text=jsonl)
        return MockResponse(content=b"evil")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    md = asyncio.run(_new_client()._download_markdown("http://x/jsonl", str(tmp_path)))

    assert md == "正文"
    # 不写穿到 tmp_path 父目录，也不写绝对路径
    assert not (tmp_path / "imgs" / "evil.jpg").exists()
    assert not (tmp_path.parent / "evil.jpg").exists()
    assert not (tmp_path.parent / "abs" / "evil2.jpg").exists()


# ═══════════════════════════════════════════════
# _submit_task：5xx 重试机制
# ═══════════════════════════════════════════════

def test_submit_task_retries_on_5xx_then_succeeds(monkeypatch, tmp_path):
    """5xx 瞬时错误：重试后成功返回 jobId"""
    calls = {"n": 0}

    async def mock_post(self, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return MockResponse({"errorMsg": "internal"}, status_code=500)
        return MockResponse({"data": {"jobId": "job-retry"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    job_id = asyncio.run(_new_client()._submit_task(str(pdf), retry_delay=0))
    assert job_id == "job-retry"
    assert calls["n"] == 2


def test_submit_task_raises_after_all_5xx_retries(monkeypatch, tmp_path):
    """5xx 全部重试耗尽 → RuntimeError"""
    calls = {"n": 0}

    async def mock_post(self, url, **kwargs):
        calls["n"] += 1
        return MockResponse({"errorMsg": "internal"}, status_code=500)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    with pytest.raises(RuntimeError, match="HTTP 500"):
        asyncio.run(_new_client()._submit_task(str(pdf), retry_delay=0))
    assert calls["n"] == 3  # 默认 retries=3


def test_submit_task_no_retry_on_4xx(monkeypatch, tmp_path):
    """4xx 客户端错误不重试，直接失败"""
    calls = {"n": 0}

    async def mock_post(self, url, **kwargs):
        calls["n"] += 1
        return MockResponse({"errorMsg": "bad request"}, status_code=400)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    with pytest.raises(RuntimeError, match="HTTP 400"):
        asyncio.run(_new_client()._submit_task(str(pdf), retry_delay=0))
    assert calls["n"] == 1


def test_submit_task_retries_when_200_without_jobid(monkeypatch, tmp_path):
    """200 但无 jobId → 重试"""
    calls = {"n": 0}

    async def mock_post(self, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return MockResponse({"data": {}})
        return MockResponse({"data": {"jobId": "job-2"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    job_id = asyncio.run(_new_client()._submit_task(str(pdf), retry_delay=0))
    assert job_id == "job-2"
    assert calls["n"] == 2


# ═══════════════════════════════════════════════
# test_connectivity：连通性测试
# ═══════════════════════════════════════════════

def test_connectivity_submits_minimal_pdf(monkeypatch):
    """连通测试：生成最小 PDF 并提交任务，返回 jobId"""
    captured = {}

    async def fake_submit(self, file_path):
        captured["file_path"] = file_path
        return "job-conn"

    monkeypatch.setattr(PaddleVLClient, "_submit_task", fake_submit)

    job_id = asyncio.run(_new_client().test_connectivity())

    assert job_id == "job-conn"
    assert captured["file_path"].endswith(".pdf")
    # 临时文件应在 finally 中清理
    import os
    assert not os.path.exists(captured["file_path"])
