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
    """httpx 响应模拟：json() / text / raise_for_status()"""

    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload if payload is not None else {}
        self._text = text
        self.status_code = status_code

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
