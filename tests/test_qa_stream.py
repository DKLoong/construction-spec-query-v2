"""API 后端 SSE 流式解析 + `/qa/ask` 单一入口的两种输出形态（T15）。"""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai.api_client import APIBackend
from app.ai.cli_client import CLIResponse   # CLI 表头用例要用（漏了会 NameError，两条用例都跑不起来）
from app.database import get_db
from app.qa import sessions as S


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


def _sse_no_done(*chunks: str) -> bytes:
    """构造**不含** `data: [DONE]` 的响应体（流自然耗尽）。

    `_sse` 无条件在末尾补 `[DONE]`，故「上游不发结束哨兵」这条路径此前无覆盖。
    """
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps(
            {"choices": [{"delta": {"content": c}}]}, ensure_ascii=False))
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _raw_after_done(tail_chunk: str) -> bytes:
    """构造 `[DONE]` 之后**仍有内容帧**的响应体（用于守卫 break 的必要性）。

    `_sse` 总把 `[DONE]` 放在末尾，而它的返回值以 `\\n\\n` 结尾，故可直接在其后拼接后置帧。
    """
    frame = ("data: " + json.dumps(
        {"choices": [{"delta": {"content": tail_chunk}}]}, ensure_ascii=False) + "\n\n")
    return _sse("before") + frame.encode("utf-8")


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
async def test_ask_stream_without_done_sentinel(monkeypatch):
    """边界场景：上游未发 `[DONE]`（流自然耗尽）→ delta 完整且仍以 done 收尾。

    夹具 `_sse` **无条件**在末尾补 `data: [DONE]`，故此前没有任何用例走过「流自然结束」
    这条路径；而 `test_ask_stream_yields_deltas` 的末条 done 断言**在有无 `[DONE]` 时都成立**，
    不能区分两条路径。真实上游（代理/网关/自建服务）不保证一定发结束哨兵，
    此时既不能丢 delta，也不能缺 done。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse_no_done("混凝土", "强度"))

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    _patch_async_client(monkeypatch, transport)

    out = [e async for e in backend.ask_stream("q")]
    assert out == [
        {"type": "delta", "text": "混凝土"},
        {"type": "delta", "text": "强度"},
        {"type": "done"},
    ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("content,error,expected", [
    ("答案", "", [{"type": "delta", "text": "答案"}, {"type": "done"}]),
    ("", "", [{"type": "done"}]),                      # 成功但空内容：不得产生空 delta
    ("", "boom", [{"type": "error", "message": "boom"}]),
])
async def test_cli_backend_ask_stream(monkeypatch, content, error, expected):
    """正常/边界/异常：CLI 后端不支持真流式，整段吐一个 delta 再 done。

    这是 `CLIBackend.ask_stream` 的**唯一**守护：删掉该方法（或让 `APIBackend` 的覆盖
    之外没有默认实现），T15 的单一入口在 CLI 后端下会直接 AttributeError。
    """
    from app.ai import cli_client

    def fake_run(prompt, work_dir=None, timeout=60):
        return CLIResponse(success=not error, content=content, error=error)

    cli = cli_client.ClaudeCodeCLI()
    monkeypatch.setattr(cli, "_run_cli", fake_run)
    out = [e async for e in cli.ask_stream("q")]
    assert out == expected


def test_guard_references_context_header():
    """一致性守卫：护栏引用的标签**就是**两条路径实际产出的那个表头。

    T7 的坑：护栏写的是【参考上下文】（全角），而 QA 实际走的 API 路径根本没有表头、
    CLI 路径用的是半角 `[参考上下文]` —— 护栏指向一个不存在的东西，退化为含糊约束。
    本用例钉的是**两条**：① 护栏的输出值含当前常量（回退到旧字面量即红）；② 护栏里不残留旧标签。
    而**常量字面值本身**由 `test_context_header_value_is_pinned` 单独钉住——
    护栏是 f-string，改常量时护栏**跟着漂**，故本用例结构上无法覆盖常量取值那一层。
    """
    from app.ai.prompts import CONTEXT_HEADER, MULTI_TURN_GUARD
    assert CONTEXT_HEADER in MULTI_TURN_GUARD
    assert "参考上下文" not in MULTI_TURN_GUARD


def test_context_header_value_is_pinned():
    """契约守卫：表头的字面值就是任务定论表规定的那个。

    其余用例全部用**符号** `CONTEXT_HEADER` 比较（比值更健壮），代价是**没有人钉住这个值本身**——
    实测把它改成任意别的标签，9 条全绿。而本 Task 的整个来由就是「护栏引用的标签必须精确」，
    标签名本身是被文档/后续任务引用的契约；且它出现在模型实际读到的护栏文本里，
    改错会让护栏重新变回「指向一个含糊标签」的状态。
    注意这是**定论表契约**的钉点，不是功能不变量——功能上护栏与表头是同源派生的，比值即可。
    """
    from app.ai.prompts import CONTEXT_HEADER
    assert CONTEXT_HEADER == "【参考条文】"


@pytest.mark.asyncio
async def test_ask_stream_ignores_frames_after_done(monkeypatch):
    """边界场景：`[DONE]` 之后的内容帧必须被丢弃。

    **删掉 `ask_stream` 里 `if payload == "[DONE]": break` 的 break（改 continue/pass）→
    本用例必须失败**：那些帧会被当作正文发出，**用户会看到本不该出现的内容**
    （`[DONE]` 是协议结束哨兵，其后帧来自上游 bug / 代理拼接 / 心跳被误当内容）。

    既有夹具里凡含 `[DONE]` 的用例，其后都没有帧，故那条 break 此前是**无覆盖的载荷分支**——
    实测把它删掉，除本用例外其余 10 条全绿。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_raw_after_done("AFTER-DONE"))

    transport = httpx.MockTransport(handler)
    backend = APIBackend("https://x/v1", "k", "m")
    _patch_async_client(monkeypatch, transport)

    out = [e async for e in backend.ask_stream("q")]
    assert [e["text"] for e in out if e["type"] == "delta"] == ["before"]


# ═══════════════════════════════════════════════════════════
# T15：流式输出并入 /qa/ask 单一入口（一个入口、两种形态）
# ═══════════════════════════════════════════════════════════

_CAND = {"id": 1, "spec_code": "GB 50204", "clause_no": "8.2.1",
         "content": "混凝土", "title": "", "spec_status": "现行"}


def _stream_backend(chunks=("混凝土", "强度")):
    """同时支持流式与非流式的桩后端。

    `command` 必须是**真字符串**：`_resolve_backend` 会读 `backend.command` 并做
    `.replace/.rsplit`，而 AsyncMock 的子属性本身也是 AsyncMock（`b.command` →
    AsyncMock、`.replace()` → coroutine），让它自动生成会在**解析后端**这一步就抛
    AttributeError，用例根本走不到断言。故显式赋字符串，与
    `tests/test_qa_relax.py:_backend` 的既有约定一致。
    """
    b = AsyncMock()
    b.is_available = lambda: True
    b.command = "fake"

    async def _gen(prompt, context="", system_prompt="", work_dir=None):
        for c in chunks:
            yield {"type": "delta", "text": c}
        yield {"type": "done"}

    b.ask_stream = _gen
    b.ask = AsyncMock(return_value=CLIResponse(
        success=True, content="".join(chunks)))
    return b


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析为 (event, data) 列表。"""
    out: list[tuple[str, dict]] = []
    ev: str | None = None
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            assert ev is not None, "data 行出现在任何 event 行之前，SSE 帧结构损坏"
            out.append((ev, json.loads(line[5:].strip())))
    return out


@pytest.fixture()
def stream_env(monkeypatch):
    """打桩检索与后端：两种输出形态都走同一套桩，且不加载模型。"""
    import app.routes.qa_routes as qr
    from app.qa.degrade import RERANK_NONE

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(dict(_CAND), 0.9)])
    monkeypatch.setattr("app.ai.cli_client.get_backend",
                        lambda name=None: _stream_backend())
    # 显式钉住模块级全局：阈值选型 `resolve_thresholds(_last_rerank_used, ...)` 读的就是它，
    # 而上面的 `_rerank_scored` 是**桩、不写该全局**——不钉住就会继承**上一条用例、乃至别的
    # 测试文件**残留下来的值（今天恰好落在可用区间，纯属巧合）。一旦某处留下
    # min_score > 0.9 的精排级别（CE 阈值可配），本文件十来条用例会**顺序相关**地翻车。
    # 取 RERANK_NONE：min_score=0、强弱按排名切分，候选必然通过筛选，与配置解耦。
    monkeypatch.setattr(qr, "_last_rerank_used", RERANK_NONE)


def _spy_search(monkeypatch) -> dict:
    """把 hybrid_search 换成记录 SearchQuery 的桩，返回记录字典。"""
    seen: dict = {}

    def fake_search(sq):
        seen["inc"] = sq.include_non_clause
        return [dict(_CAND)], 1

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)
    return seen


def test_default_is_non_streaming_json(auth_client, stream_env):
    """回归（入口合并的核心契约）：不带 stream 时仍是原 JSON 响应。

    既有 tests/test_qa_routes.py 全部依赖该行为，合并入口不得改变它。
    """
    r = auth_client.post("/qa/ask", json={"question": "q"})
    assert "text/event-stream" not in r.headers["content-type"]
    assert "answer" in r.json()


def test_stream_emits_stage_then_deltas_then_done(auth_client, stream_env):
    """正常场景：stream=true 时事件顺序为 stage → delta×N → done。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    kinds = [e for e, _ in _parse_sse(r.text)]
    assert kinds[0] == "stage", "首个事件必须是阶段进度（覆盖流式前的死时间）"
    assert kinds[-1] == "done"
    assert "delta" in kinds


def test_first_stage_frame_precedes_retrieval_and_rerank(auth_client, stream_env,
                                                         monkeypatch):
    """回归（本 Task 的核心契约）：首帧 stage 必须**先于**检索/精排发出。

    为什么必须有这条：`_prepare_qa_context` 是**同步**函数，hybrid_search + CE 精排
    （跑模型，设计文档 §4.11 记 1~3 秒）全在它内部。若准备发生在 `_sse_stream` 之前
    （首版实现：qa_ask 先 prepare、再返回 StreamingResponse），首帧 stage 只能在死时间
    **结束后**补发——retrieving/generating 与 delta 一起涌出，分阶段进度提示形同虚设。
    这正是设计文档 :367 / :404 明写要覆盖的那段死时间；本用例是它唯一的守卫。

    **接缝为什么选「生成器 yield 边界」而不是 `client.stream` 逐行读**：
    本仓 TestClient 走 starlette `_TestClientTransport.handle_request`
    （starlette/testclient.py:394-396）——它把整段响应体写进 `io.BytesIO`，
    `portal.call(self.app, ...)` **跑完才返回 Response**；httpx 的 ASGITransport
    （httpx/_transports/asgi.py:176-183）同样先把 body 全收进 `body_parts`。
    **两条路径都全量缓冲**：客户端拿到第一帧时，应用侧整个生成器早已跑完，
    故「客户端先读到第一帧、此刻 prep 尚未被调用」在结构上**不可观测**（不是没写，
    是这套 TestClient 观测不到）。退而取最强替代：包装 `_sse_stream`，在帧**被产出、
    即将下传**的那一刻记账，与 `_prepare_qa_context` 的调用记入**同一条时间线**
    （单线程顺序执行，无竞态）。下面再补一条「记账的帧序列 == 客户端实际收到的帧序列」，
    排除「记账与真实流不一致」的怀疑。
    """
    import app.routes.qa_routes as qr

    real_stream, real_prep = qr._sse_stream, qr._prepare_qa_context
    timeline: list[tuple[str, str, str]] = []      # (来源, 事件名, stage)

    def _spy_prep(question, body):
        timeline.append(("prep", "", ""))
        return real_prep(question, body)

    def _spy_stream(question, body):
        async def _gen():
            async for frame in real_stream(question, body):
                ev, data = _parse_sse(frame)[0]
                timeline.append(("yield", ev, data.get("stage", "")))
                yield frame
        return _gen()

    monkeypatch.setattr(qr, "_prepare_qa_context", _spy_prep)
    monkeypatch.setattr(qr, "_sse_stream", _spy_stream)

    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    frames = _parse_sse(r.text)

    assert [ev for src, ev, _ in timeline if src == "yield"] == [e for e, _ in frames], \
        "记账的帧序列必须与客户端实际收到的帧序列一致，否则本用例守的是别的东西"
    assert timeline[0] == ("yield", "stage", "retrieving"), \
        f"首帧 stage 必须先于 _prepare_qa_context（检索 + CE 精排）；实测序列：{timeline[:3]}"
    assert timeline[1] == ("prep", "", ""), "准备必须在首帧之后立即发生"
    assert [stage for src, ev, stage in timeline if ev == "stage"] == \
        ["retrieving", "generating"], \
        "只发两帧：retrieving 覆盖整个准备段、generating 覆盖模型段；" \
        "_prepare_qa_context 是同步函数、无法从内部 yield，拆出 reranking 是假装能区分"


def test_stream_empty_output_reuses_json_hint(auth_client, stream_env, monkeypatch):
    """边界场景：后端成功但整轮没有 delta → 流式必须补出与非流式**逐字相同**的提示。

    真实形态：`CLIBackend.ask_stream` 在「成功但内容为空」时**只 yield done**
    （cli_client.py:167-170）。若 `_sse_stream` 照搬，就是「一条 delta 都没有、
    done 里也不含文案」→ 前端显示**空白回答且无任何提示**；而同样状态下非流式会返回
    `_answer_text` 的空内容文案。两条形态合并的初衷正是不让行为漂移。

    期望值由**非流式响应现算**（不是抄进用例的字面量）：实现里任一处改文案而另一处
    不改，本用例即红；只补 done 不补 delta，也红。
    """
    b = AsyncMock()
    b.is_available = lambda: True
    b.command = "fake"      # 真字符串，理由见 _stream_backend docstring
    b.ask = AsyncMock(return_value=CLIResponse(success=True, content=""))

    async def _done_only(prompt, context="", system_prompt="", work_dir=None):
        yield {"type": "done"}      # 空内容时 CLI 后端的真实形态

    b.ask_stream = _done_only
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)

    json_answer = auth_client.post("/qa/ask", json={"question": "q"}).json()["answer"]

    frames = _parse_sse(auth_client.post(
        "/qa/ask", json={"question": "q", "stream": True}).text)
    assert [d["text"] for e, d in frames if e == "delta"] == [json_answer], \
        "流式空内容的提示必须与非流式逐字相同（同一文案来源）"
    assert "error" not in [e for e, _ in frames], "成功但空内容不是错误路径"
    assert [e for e, _ in frames][-1] == "done"


def test_stream_done_carries_session_id_and_sources(auth_client, stream_env):
    """正常场景：done 事件携带会话 id 与参考条文，供前端收尾渲染。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    done = [d for e, d in _parse_sse(r.text) if e == "done"][0]
    assert done["session_id"] > 0
    assert done["sources"][0]["clause_no"] == "8.2.1"


def test_stream_persists_messages_on_success(auth_client, stream_env):
    """正常场景：流式成功后消息落库（与非流式行为一致）。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    sid = [d for e, d in _parse_sse(r.text) if e == "done"][0]["session_id"]
    assert [m["role"] for m in S.get_messages(sid)] == ["user", "assistant"]


def test_stream_error_event_when_backend_fails(auth_client, stream_env, monkeypatch):
    """异常场景：后端报错时发 error 事件，且整轮不落库。"""
    b = AsyncMock()
    b.is_available = lambda: True
    b.command = "fake"      # 真字符串，理由见 _stream_backend docstring

    async def _gen(prompt, context="", system_prompt="", work_dir=None):
        yield {"type": "error", "message": "API 调用超时"}

    b.ask_stream = _gen
    # 显式覆盖 stream_env 的桩：本用例的后端必须在**流中途**报错
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)

    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    assert _parse_sse(r.text)[-1][0] == "error"
    assert S.list_sessions() == [], "失败轮次不得建库"


def test_stream_backend_exception_emits_error_frame_and_traces(auth_client, stream_env,
                                                               monkeypatch):
    """异常场景：`ask_stream` **抛异常**（而非发 error 帧）→ 补 error 帧 **且** 写埋点。

    200 与两帧 stage 都已发出，异常若逃逸到 Starlette：
    ① 客户端只看到连接中断，拿不到任何错误文案（`stream_response` 只把 OSError 转成
       ClientDisconnect，其余异常一路抛出）；
    ② `_emit_trace` 永不执行 → 这条失败请求在日志 Tab 里**根本不存在**。
    而紧邻的 error 分支特意写了 `_emit_trace`（注释「失败也埋点，供排查」）——
    两条失败路径必须同规格。这正是本用例守住的：去掉那个 try/except，异常直接冲出
    TestClient，既断不到 error 帧、也查不到埋点行。
    """
    b = AsyncMock()
    b.is_available = lambda: True
    b.command = "fake"

    async def _boom(prompt, context="", system_prompt="", work_dir=None):
        raise RuntimeError("上游连接被重置")
        yield      # pragma: no cover —— 有 yield 才是 async generator

    b.ask_stream = _boom
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)

    r = auth_client.post("/qa/ask", json={"question": "流式异常埋点检查", "stream": True})
    frames = _parse_sse(r.text)
    # 异常发生在两帧 stage 之后，已发出的进度帧不得丢失
    assert [(e, d.get("stage", "")) for e, d in frames if e == "stage"] == \
        [("stage", "retrieving"), ("stage", "generating")]
    assert frames[-1] == ("error", {"message": "AI 服务返回错误"})
    assert S.list_sessions() == [], "失败轮次不得建库"
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM qa_request_logs WHERE question LIKE ?",
            ("%流式异常埋点检查%",),
        ).fetchone()[0]
    assert n == 1, "抛异常的流式轮次未落埋点（try/except 只做了一半）"


def test_stream_continues_into_existing_session(auth_client, stream_env):
    """正常场景：带 session_id 的流式请求续聊同一会话。"""
    sid = S.create_session("s")
    r = auth_client.post("/qa/ask",
                         json={"question": "q", "session_id": sid, "stream": True})
    done = [d for e, d in _parse_sse(r.text) if e == "done"][0]
    assert done["session_id"] == sid
    assert len(S.get_messages(sid)) == 2


def test_stream_empty_question_returns_400(auth_client, stream_env):
    """异常场景：空问题返回 400，不建立 SSE 流。

    除状态码外**必须钉住 body**：`{"detail": ...}` 是本项目统一的错误形态
    （前端按 `detail` 取文案），只断 400 的话换成空体 / 别的结构照样绿。
    """
    r = auth_client.post("/qa/ask", json={"question": "   ", "stream": True})
    assert r.status_code == 400
    assert r.json()["detail"] == "问题不能为空"
    assert "text/event-stream" not in r.headers["content-type"]


def test_stream_request_is_traced(auth_client, stream_env):
    """回归（评审修复项）：流式请求必须落 qa_request_logs。

    首版设计的新增路由漏了 _emit_trace，导致走流式的问答（主路径）
    在日志 Tab 中完全不可见。合并入口后，埋点在两条路径上都生效。
    """
    auth_client.post("/qa/ask", json={"question": "流式埋点检查", "stream": True})
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM qa_request_logs WHERE question LIKE ?",
            ("%流式埋点检查%",),
        ).fetchone()[0]
    assert n == 1, "流式请求未落埋点表"


def test_non_stream_request_is_traced(auth_client, stream_env):
    """回归：非流式请求同样落埋点（合并入口不得丢失既有行为）。"""
    auth_client.post("/qa/ask", json={"question": "非流式埋点检查"})
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM qa_request_logs WHERE question LIKE ?",
            ("%非流式埋点检查%",),
        ).fetchone()[0]
    assert n == 1


def test_include_non_clause_flag_is_honored(auth_client, stream_env, monkeypatch):
    """回归（静默 no-op）：左栏「包含前言·条文说明」必须真的到达检索层。

    QaRequest 未声明该字段时，Pydantic 默认 extra='ignore' 会静默丢弃它——
    前端传了也不生效，且没有任何报错。本用例捕获 SearchQuery 断言开关生效。
    """
    seen = _spy_search(monkeypatch)
    auth_client.post("/qa/ask", json={"question": "q", "include_non_clause": True})
    assert seen.get("inc") is True, "include_non_clause 未到达检索层（字段未声明？）"


def test_question_text_fallback_still_releases_non_clause(auth_client, stream_env,
                                                          monkeypatch):
    """边界场景：问题文本含「条文说明」时隐式放行。

    这是设计文档 §4.3 的兜底条款——新增显式开关后，文本兜底不得失效。
    """
    seen = _spy_search(monkeypatch)
    auth_client.post("/qa/ask", json={"question": "条文说明里怎么写的"})
    assert seen.get("inc") is True


def test_include_non_clause_false_and_plain_question_stays_hidden(
        auth_client, stream_env, monkeypatch):
    """边界场景（否定对照）：复选框未勾 + 问题文本无关键词 → 必须仍然不放行。

    上面两条用例只覆盖了两个「真」分支，一条恒为 True 的实现能把它们全骗过去
    （见 task-15-report「变异 M5」：把或式塌缩为常量 True 时，上面两条仍全绿）。
    「复选框 **或** 文本兜底」是或关系，其真假表必须有「全假 → 假」这一格。
    """
    seen = _spy_search(monkeypatch)
    auth_client.post("/qa/ask",
                     json={"question": "q", "include_non_clause": False})
    assert seen.get("inc") is False, "未勾选且文本无关键词时不得放行非条文"


def test_effective_filters_recorded_with_assistant_message(auth_client, stream_env):
    """正常场景：当轮生效筛选随助手消息落库（D5），回看可追溯。"""
    auth_client.post("/qa/ask", json={"question": "q", "dim4_specialty": ["混凝土"]})
    sid = S.list_sessions()[0]["id"]
    msgs = S.get_messages(sid)
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["filters"]["dim4_specialty"] == ["混凝土"]


def test_effective_filters_include_non_clause_only_when_explicit(
        auth_client, stream_env):
    """回归（`_effective_filters` 新增那行）：落库 filters 里的 include_non_clause
    **只反映复选框**，不含问题文本触发的隐式兜底。

    （a）复选框显式勾选 → `filters["include_non_clause"] is True`；
    （b）**仅**问题文本含「条文说明」触发检索层兜底 → filters 里**没有**该键。
    （b）是**有意的设计**（见 `_effective_filters` docstring）：文本兜底是隐式放行，
    不是用户对本轮筛选的选择；记进去会让回看时「这条答案在什么筛选下产生」失真。
    两个方向必须同时钉住——只钉 (a)，把兜底也「顺手修」进去照样绿（本轮之前的
    变异 M18 实证：30 passed + QA 相关 7 个文件 131 passed 全绿）；
    只钉 (b)，删掉那行 `if body.include_non_clause:` 照样绿。
    """
    auth_client.post("/qa/ask", json={"question": "q", "include_non_clause": True})
    sids_a = [s["id"] for s in S.list_sessions()]
    assert len(sids_a) == 1
    assert S.get_messages(sids_a[0])[1]["filters"]["include_non_clause"] is True

    auth_client.post("/qa/ask", json={"question": "条文说明里怎么写的"})
    sids_b = [s["id"] for s in S.list_sessions()]
    assert len(sids_b) == 2, "第二条问题应新建会话"
    sid_b = next(i for i in sids_b if i not in sids_a)
    filters_b = S.get_messages(sid_b)[1]["filters"]
    assert "include_non_clause" not in filters_b, \
        "文本兜底不是用户设置的筛选，不得记入 filters"
    assert filters_b == {}, "该轮没有任何显式筛选，filters 应为空"


def test_done_rerank_used_is_not_read_from_global(auth_client, stream_env, monkeypatch):
    """回归（竞态）：done 里的 rerank_used 必须是本请求的值。

    首版设计在流式循环**之后**读模块级全局 _last_rerank_used——
    期间任何并发请求都会覆盖它。本用例在流式进行中篡改该全局，
    模拟并发干扰，断言 done 事件不受影响。
    """
    import app.routes.qa_routes as qr

    async def _gen(prompt, context="", system_prompt="", work_dir=None):
        yield {"type": "delta", "text": "第一段"}
        # 模拟另一并发请求在本请求流式期间改写了全局
        qr._last_rerank_used = "vector"
        yield {"type": "delta", "text": "第二段"}
        yield {"type": "done"}

    b = AsyncMock()
    b.is_available = lambda: True
    b.command = "fake"      # 真字符串，理由见 _stream_backend docstring
    b.ask_stream = _gen
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)
    monkeypatch.setattr(qr, "_last_rerank_used", "crossencoder")

    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    done = [d for e, d in _parse_sse(r.text) if e == "done"][0]
    assert done["rerank_used"] == "crossencoder", \
        "done 必须回报本请求的精排级别，不能被并发请求改写"


def test_done_rerank_used_comes_from_prepared_copy(auth_client, stream_env,
                                                   monkeypatch):
    """结构守卫：done 的 rerank_used 取自**准备阶段的拷贝**，而非收尾期回读 trace。

    `ctx.rerank_used` 与 `ctx.trace.rerank_used` 在正常流程下恒等（同源赋值），
    故上一条竞态用例**结构上无法区分**这两个载体——把 done 载荷写成
    `ctx.trace.rerank_used` 它照样全绿。本用例在收尾前把 trace 里那一份改成
    哨兵值，模拟「trace 在流式期间被别处改写」，断言 done 取的仍是准备阶段那份。
    """
    import app.routes.qa_routes as qr

    real_finish = qr._finish_turn

    def _finish_then_poison(ctx, answer, persist_ok, body):
        ctx.trace.rerank_used = "POISON"    # 收尾期被改写的那一份
        return real_finish(ctx, answer, persist_ok, body)

    monkeypatch.setattr(qr, "_finish_turn", _finish_then_poison)
    # 显式钉住全局：本模块是进程级状态，别的测试文件跑过真实 _rerank_scored 会留下残值
    monkeypatch.setattr(qr, "_last_rerank_used", "crossencoder")

    r = auth_client.post("/qa/ask", json={"question": "q", "stream": True})
    done = [d for e, d in _parse_sse(r.text) if e == "done"][0]
    assert done["rerank_used"] == "crossencoder", \
        "done 必须取准备阶段的拷贝值，不得回读收尾期被改写的 trace"
