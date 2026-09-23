"""API 后端 SSE 流式解析。"""
import json

import httpx
import pytest

from app.ai.api_client import APIBackend
from app.ai.cli_client import CLIResponse   # CLI 表头用例要用（漏了会 NameError，两条用例都跑不起来）


_REAL_ASYNC_CLIENT = httpx.AsyncClient   # 必须在 monkeypatch 之前捕获真实类，见下方说明


def _patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    """把 httpx.AsyncClient 换成走 MockTransport 的客户端。

    ⚠️ 必须在 lambda **外部**先捕获真实类：``monkeypatch.setattr(httpx, "AsyncClient", ...)``
    会把模块属性换成该 lambda 本身，而 lambda 体内的 ``httpx.AsyncClient`` 是**调用时**
    才解析的——于是它解析到自己，变成「自己调自己且 transport 传了两次」，
    抛 ``TypeError: got multiple values for keyword argument 'transport'``。
    简报首版正是这个写法，9 条里有 6 条因此根本没跑到断言（见 task-14-report.md）。
    """
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: _REAL_ASYNC_CLIENT(transport=transport, **kw),
    )


def _sse(*chunks: str) -> bytes:
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps(
            {"choices": [{"delta": {"content": c}}]}, ensure_ascii=False))
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


@pytest.mark.asyncio
async def test_ask_stream_yields_deltas(monkeypatch):
    """正常场景：逐块解析 delta，最后一条为 done。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse("混凝土", "强度", "等级"))

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    _patch_async_client(monkeypatch, transport)

    out = [e async for e in backend.ask_stream("q")]
    assert [e["text"] for e in out if e["type"] == "delta"] == ["混凝土", "强度", "等级"]
    assert out[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_ask_stream_reports_http_error(monkeypatch):
    """异常场景：HTTP 错误转成 error 事件，不抛异常中断 SSE 流。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"{}")

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    _patch_async_client(monkeypatch, transport)

    out = [e async for e in backend.ask_stream("q")]
    assert out[-1]["type"] == "error"
    assert "429" in out[-1]["message"] or "超限" in out[-1]["message"]


@pytest.mark.asyncio
async def test_ask_stream_handles_empty_stream(monkeypatch):
    """边界场景：只有 [DONE] 无内容 → 仅 done，不产生空 delta。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    _patch_async_client(monkeypatch, transport)

    out = [e async for e in backend.ask_stream("q")]
    assert out == [{"type": "done"}]


@pytest.mark.asyncio
async def test_ask_stream_skips_malformed_lines(monkeypatch):
    """异常场景：脏 SSE 行被跳过，不中断整个流。"""
    body = b'data: not-json\n\ndata: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    _patch_async_client(monkeypatch, transport)

    out = [e async for e in backend.ask_stream("q")]
    assert [e["text"] for e in out if e["type"] == "delta"] == ["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("via_stream", [True, False])
async def test_api_backend_emits_context_header(monkeypatch, via_stream):
    """正常场景：API 路径的提示词带条文段表头（首版 API 路径**根本没有表头**）。

    **参数化两条路径是有意的**：本 Task 要求 `ask()` 与 `ask_stream()` 共用
    `_build_messages`（否则「共用同一套构造」是假话）。若只测流式，把 `ask()` 改回
    自己拼串（不看表头常量）也能全绿——那条路径就会悄悄漂移。
    """
    from app.ai.prompts import CONTEXT_HEADER

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        if via_stream:
            return httpx.Response(200, content=_sse("ok"))
        return httpx.Response(200, content=json.dumps(
            {"choices": [{"message": {"content": "ok"}}]}).encode("utf-8"))

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)
    backend = APIBackend("https://x/v1", "k", "m")
    if via_stream:
        _ = [e async for e in backend.ask_stream("q", context="条文正文")]
    else:
        await backend.ask("q", context="条文正文")

    user_msg = captured["body"]["messages"][1]["content"]
    # 删掉 _build_messages 里的表头拼接 → 本断言失败（两条路径都要红）
    assert user_msg.startswith(CONTEXT_HEADER)
    assert "条文正文" in user_msg
    assert "参考上下文" not in user_msg, "旧标签必须彻底消失"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls_name", ["ClaudeCodeCLI", "CodexCLI"])
async def test_cli_backends_emit_context_header(monkeypatch, cls_name):
    """正常场景：**两个** CLI 后端的提示词都带同一条文段表头。

    参数化两个类是有意的：`cli_client.py` 里这段拼装是**两份副本**，
    只改一处时另一处必须红——否则「两处副本同步」这件事没有任何用例守着。
    """
    from app.ai import cli_client
    from app.ai.prompts import CONTEXT_HEADER

    seen: dict = {}

    def fake_run(prompt, work_dir=None, timeout=60):
        seen["p"] = prompt
        return CLIResponse(success=True, content="ok")

    backend = getattr(cli_client, cls_name)()
    monkeypatch.setattr(backend, "_run_cli", fake_run)
    await backend.ask("q", context="条文正文")

    assert f"{CONTEXT_HEADER}\n条文正文" in seen["p"]
    assert "参考上下文" not in seen["p"]


def test_guard_references_context_header():
    """一致性守卫：护栏引用的标签**就是**两条路径实际产出的那个表头。

    T7 的坑：护栏写的是【参考上下文】（全角），而 QA 实际走的 API 路径根本没有表头、
    CLI 路径用的是半角 `[参考上下文]` —— 护栏指向一个不存在的东西，退化为含糊约束。
    本用例钉住「常量 == 护栏引用的标签」：改常量不改护栏、或把护栏改回字面量，都必须红。
    """
    from app.ai.prompts import CONTEXT_HEADER, MULTI_TURN_GUARD
    assert CONTEXT_HEADER in MULTI_TURN_GUARD
    assert "参考上下文" not in MULTI_TURN_GUARD
