"""QA 路由的会话行为（惰性创建、续聊、失败不入库）。

测试基础设施见本计划「测试基础设施」节，两条硬性要求：
  1. 用 conftest 的 auth_client（已建库/建用户/登录），不要裸 TestClient；
  2. patch **函数内局部 import 的源模块**，不要 patch 路由模块。
"""
from unittest.mock import AsyncMock

import pytest

from app.ai.cli_client import CLIResponse
from app.qa import sessions as S

# 固定候选（stub 用），字段与真实 hybrid_search 返回对齐
_CAND = {"id": 1, "spec_code": "GB 50204", "clause_no": "8.2.1",
         "content": "混凝土强度等级应…", "title": "", "spec_status": "现行"}


def _fake_backend(answer="答案"):
    b = AsyncMock()
    b.is_available = lambda: True
    # 显式给出命令名：AsyncMock 自动生成的属性是**AsyncMock**（调用返回协程），
    # 而路由会用 `(backend.command or "cli").replace(...)` 取可读命令名——
    # 真实后端的 command 本就是 str，故此处必须按真实形态给出字符串。
    b.command = "fake"
    b.ask = AsyncMock(return_value=CLIResponse(success=True, content=answer))
    return b


@pytest.fixture()
def qa_env(monkeypatch):
    """打桩检索与后端：QA 链路完全确定，且不加载任何模型。

    注：get_backend 被调用时只传一个位置参数（body.backend），
    故此处签名为 (name=None)。
    """
    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(dict(_CAND), 0.9)])
    monkeypatch.setattr("app.ai.cli_client.get_backend",
                        lambda name=None: _fake_backend())


def test_first_ask_creates_session(auth_client, qa_env):
    """正常场景：首次问答（无 session_id）惰性建会话，标题取问题前 20 字。"""
    r = auth_client.post("/qa/ask", json={"question": "混凝土强度等级如何评定"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    got = S.get_session(sid)
    assert got is not None
    assert got["title"] == "混凝土强度等级如何评定"


def test_ask_persists_both_messages(auth_client, qa_env):
    """正常场景：一轮问答落两条消息（user + assistant）。"""
    sid = auth_client.post("/qa/ask", json={"question": "q"}).json()["session_id"]
    msgs = S.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "答案"


def test_second_ask_same_session_appends(auth_client, qa_env):
    """正常场景：带 session_id 续聊，消息追加而非新建会话。"""
    sid = auth_client.post("/qa/ask", json={"question": "q1"}).json()["session_id"]
    r2 = auth_client.post("/qa/ask", json={"question": "q2", "session_id": sid})
    assert r2.json()["session_id"] == sid
    assert len(S.get_messages(sid)) == 4


def test_unknown_session_id_creates_new_session(auth_client, qa_env):
    """异常场景：session_id 指向不存在的会话 → 视为新会话，不报错、不跨会话取历史。"""
    r = auth_client.post("/qa/ask", json={"question": "q", "session_id": 999999})
    assert r.status_code == 200
    assert r.json()["session_id"] != 999999


def test_history_is_injected_on_second_turn(auth_client, monkeypatch):
    """正常场景：第二轮的 context 中含第一轮问答，且不含本轮问题。

    直接断言喂给后端的上下文，比断言某个函数被调用更强——它验证的是
    可观测的结果（历史真的进了 prompt），而非实现细节。
    """
    captured = {}

    async def _ask(prompt, context="", system_prompt="", work_dir=None):
        captured["context"] = context
        return CLIResponse(success=True, content="答案")

    b = AsyncMock()
    b.is_available = lambda: True
    b.command = "fake"  # 同 _fake_backend：路由要读 str 形态的命令名
    b.ask = _ask
    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(dict(_CAND), 0.9)])
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)

    first_q = "第一轮问题"
    sid = auth_client.post("/qa/ask", json={"question": first_q}).json()["session_id"]
    auth_client.post("/qa/ask", json={"question": "第二轮问题", "session_id": sid})

    assert first_q in captured["context"], "第二轮必须注入第一轮问答作为历史"
    assert "第二轮问题" not in captured["context"], "本轮问题不得混进历史段"


def test_failed_llm_call_not_persisted(auth_client, qa_env, monkeypatch):
    """异常场景：LLM 调用失败时整轮不入库，避免半截会话污染历史。"""
    failing = AsyncMock()
    failing.is_available = lambda: True
    failing.command = "fake"  # 同 _fake_backend：路由要读 str 形态的命令名
    failing.ask = AsyncMock(return_value=CLIResponse(
        success=False, content="", error="API 调用超时"))
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: failing)

    r = auth_client.post("/qa/ask", json={"question": "q"})
    assert r.status_code == 200
    # 失败时助手消息不入库；用户消息也不入库（整轮作废）
    assert S.list_sessions() == []


def test_trace_records_history_tokens_and_budget(auth_client, qa_env):
    """埋点落库历史段 token 与预算（本 Task 硬要求：超预算必须可观测）。

    build_history 是纯函数、无 IO，超预算本身不留任何痕迹，故必须由调用方
    把「实际值 + 预算」写进 qa_request_logs——否则调参只能靠猜。此处钉住
    两个字段确实进入了 INSERT（该表是唯一的分位数数据来源）。
    """
    from app.database import get_db

    sid = auth_client.post("/qa/ask", json={"question": "q1"}).json()["session_id"]
    auth_client.post("/qa/ask", json={"question": "q2", "session_id": sid})

    with get_db() as conn:
        row = conn.execute(
            "SELECT history_tokens, history_budget FROM qa_request_logs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["history_tokens"] > 0, "第二轮应记录历史段实际 token"
    assert row["history_budget"] > 0, "应记录历史段预算"
