"""Task 4: APIBackend 单元测试"""
import pytest


def test_api_backend_import():
    """APIBackend 可导入"""
    from app.ai.api_client import APIBackend
    assert APIBackend is not None


def test_api_backend_inherits_cli_backend():
    """APIBackend 继承 CLIBackend"""
    from app.ai.api_client import APIBackend
    from app.ai.cli_client import CLIBackend
    assert issubclass(APIBackend, CLIBackend)


def test_api_backend_is_available_with_key():
    """有 api_key 时 is_available() 返回 True"""
    from app.ai.api_client import APIBackend
    backend = APIBackend(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )
    assert backend.is_available() is True


def test_api_backend_is_not_available_without_key():
    """无 api_key 时 is_available() 返回 False"""
    from app.ai.api_client import APIBackend
    backend = APIBackend(
        base_url="https://api.example.com/v1",
        api_key="",
        model="test-model",
    )
    assert backend.is_available() is False


def test_api_backend_ask_makes_http_call(monkeypatch):
    """ask() 发送 HTTP POST 到 /chat/completions"""
    from app.ai.api_client import APIBackend

    class MockResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{
                    "message": {"content": "根据规范，模板应能承受混凝土侧压力。"}
                }]
            }

        def raise_for_status(self):
            pass

    import httpx
    captured_url = []
    captured_json = []
    captured_headers = []

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, headers=None, timeout=None):
            captured_url.append(url)
            captured_json.append(json)
            captured_headers.append(headers)
            return MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)

    backend = APIBackend(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-chat",
    )

    import asyncio
    resp = asyncio.run(backend.ask(
        prompt="模板设计要求",
        context="[GB 50204 5.1.1] 模板应能承受混凝土侧压力。",
    ))

    assert resp.success is True
    assert "模板" in resp.content
    assert captured_url[0] == "https://api.deepseek.com/chat/completions"
    assert captured_json[0]["model"] == "deepseek-chat"
    assert captured_headers[0]["Authorization"] == "Bearer sk-test"


def test_provider_presets():
    """PROVIDERS 包含所有预置厂商"""
    from app.ai.provider_presets import PROVIDERS
    assert "doubao" in PROVIDERS
    assert "deepseek" in PROVIDERS
    assert "glm" in PROVIDERS
    assert "kimi" in PROVIDERS
    assert PROVIDERS["doubao"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert PROVIDERS["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert PROVIDERS["glm"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert PROVIDERS["kimi"]["base_url"] == "https://api.moonshot.cn/v1"


def test_api_backend_handles_429_error(monkeypatch):
    """429 错误返回友好的错误信息"""
    from app.ai.api_client import APIBackend

    class MockErrorResponse:
        status_code = 429

        def json(self):
            return {"error": {"message": "Rate limit exceeded"}}

        def raise_for_status(self):
            import httpx
            raise httpx.HTTPStatusError("429", request=None, response=self)

    import httpx

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return MockErrorResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)

    backend = APIBackend("https://api.test.com", "sk-test", "model")
    import asyncio
    resp = asyncio.run(backend.ask(prompt="test"))
    assert resp.success is False
    assert "429" in resp.error or "超限" in resp.error
