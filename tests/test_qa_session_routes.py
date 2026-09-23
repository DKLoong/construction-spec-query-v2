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


def test_list_sessions_endpoint(auth_client):
    """正常场景：列表接口返回会话数组，按最近活跃倒序。"""
    a = S.create_session("A")
    S.append_message(a, "user", "q")
    r = auth_client.get("/qa/sessions")
    assert r.status_code == 200
    assert r.json()["sessions"][0]["id"] == a


def test_list_sessions_empty_returns_empty_array(auth_client):
    """边界场景：无会话时返回空数组（统一结构，禁止返回 null）。"""
    r = auth_client.get("/qa/sessions")
    assert r.json() == {"sessions": []}


def test_get_session_detail_returns_messages_with_sources(auth_client):
    """正常场景：详情返回会话元信息 + 全部消息，含 sources（前端据此重建条文链接）。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    S.append_message(sid, "assistant", "a", sources=[{"code": "GB 50204",
                                                      "clause_no": "8.2.1"}])
    r = auth_client.get(f"/qa/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["title"] == "s"
    assert body["messages"][1]["sources"][0]["clause_no"] == "8.2.1"


def test_get_missing_session_returns_404(auth_client):
    """异常场景：不存在的会话返回 404，而非空对象。

    必须**同时断言 body**：404 是框架在「路由未注册」时也会产生的状态码
    （body 为 `{"detail": "Not Found"}`），只断状态码的用例在裸 FastAPI 应用上
    同样会通过 —— 那样的断言对它名字里的行为无法失败。只有 body 才能区分
    「我们的 404」与「框架的 404」。
    """
    r = auth_client.get("/qa/sessions/999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在"


def test_rename_session_endpoint(auth_client):
    """正常场景：重命名生效。"""
    sid = S.create_session("旧名")
    r = auth_client.patch(f"/qa/sessions/{sid}", json={"title": "新名"})
    assert r.status_code == 200 and r.json()["ok"] is True
    got = S.get_session(sid)
    assert got is not None
    assert got["title"] == "新名"


def test_rename_rejects_blank_title(auth_client):
    """异常场景：空白标题被拒（400），不得写库。"""
    sid = S.create_session("原名")
    r = auth_client.patch(f"/qa/sessions/{sid}", json={"title": "   "})
    assert r.status_code == 400
    got = S.get_session(sid)
    assert got is not None
    assert got["title"] == "原名"


def test_rename_missing_session_returns_404(auth_client):
    """异常场景：不存在的会话返回 404。

    ⚠️ 同样必须断言 body——只断状态码时，路由缺失也会因 FastAPI 默认 404 而假通过。
    """
    r = auth_client.patch("/qa/sessions/999999", json={"title": "x"})
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在"


def test_rename_title_length_enforced(auth_client):
    """边界场景：超长标题被截断到上限，不拒绝（用户体验优先）。"""
    sid = S.create_session("s")
    r = auth_client.patch(f"/qa/sessions/{sid}", json={"title": "长" * 200})
    assert r.status_code == 200
    got = S.get_session(sid)
    assert got is not None
    assert len(got["title"]) <= 100
    # 追加断言（brief 只给 <=100，无法区分「截断到 100」与「截断到任意更短值」）：
    # 钉住上限本身，否则把 _SESSION_TITLE_MAX 改成 5 也照样通过。
    assert len(got["title"]) == 100


def test_delete_session_endpoint_removes_messages(auth_client):
    """正常场景：删除会话后消息一并清除。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "q")
    r = auth_client.delete(f"/qa/sessions/{sid}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert S.get_session(sid) is None and S.get_messages(sid) == []


def test_delete_missing_session_returns_404(auth_client):
    """异常场景：删除不存在的会话返回 404。

    ⚠️ 同样必须断言 body——只断状态码时，路由缺失也会因 FastAPI 默认 404 而假通过。
    """
    r = auth_client.delete("/qa/sessions/999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在"


def test_build_markdown_contains_title_and_turns():
    """正常场景：导出内容含会话名、问答正文与参考条文。"""
    sess = {"id": 1, "title": "混凝土强度", "created_at": "2026-09-21 10:00:00",
            "updated_at": "2026-09-21 10:05:00"}
    msgs = [{"role": "user", "content": "如何评定", "sources": [], "created_at": "x"},
            {"role": "assistant", "content": "按 GB 50204 评定",
             "sources": [{"code": "GB 50204", "clause_no": "8.2.1"}],
             "created_at": "x"}]
    md = S.build_markdown(sess, msgs)
    assert "# 混凝土强度" in md
    assert "如何评定" in md
    # 引文格式为「《规范编号》条文号」——项目惯例，与 app/ai/prompts.py:11,23、
    # app/qa/context.py:92、app/templates/partials/qa_panel.html:49、
    # static/components/qa.js:93,111 等 7 处渲染一致（qa.js 还会用
    # `【《code》clause】` 正则把它转成条文链接）。brief 原断言写作空格分隔的
    # `"GB 50204 8.2.1"` 是笔误：它不可能是 `《GB 50204》8.2.1` 的子串。
    assert "《GB 50204》8.2.1" in md
    # 追加断言（brief 的三条对「user/assistant 弄反」无法失败——两段正文无论
    # 谁挂谁都在 md 里）。钉住轮次顺序与「问/答」标签的归属。
    assert md.index("## 问") < md.index("如何评定") \
        < md.index("## 答") < md.index("按 GB 50204 评定")
    # 同上：参考条文若被摘掉 `> 参考条文：` 前缀，上面那条 `in md` 照样通过
    assert "> 参考条文：《GB 50204》8.2.1" in md


def test_build_markdown_handles_session_without_messages():
    """边界场景：空会话导出不报错，仍含标题。"""
    md = S.build_markdown({"title": "空会话", "created_at": "", "updated_at": ""}, [])
    assert "# 空会话" in md
    # 追加断言：零消息不得凭空渲染出轮次标题
    assert "## 问" not in md and "## 答" not in md


def test_export_endpoint_returns_markdown_attachment(auth_client):
    """正常场景：导出接口返回 markdown 附件，带 Content-Disposition。"""
    sid = S.create_session("混凝土强度")
    S.append_message(sid, "user", "如何评定")
    r = auth_client.get(f"/qa/sessions/{sid}/export")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "如何评定" in r.text
    # 追加断言（brief 只断 "attachment"，对「改成裸 filename="中文.md"」无法失败：
    # 裸文件名同样含 attachment 字样，而中文名在多数浏览器下会乱码）。
    # 故钉住 RFC 5987 的 filename* 及其百分号编码结果。
    from urllib.parse import quote
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert quote("混凝土强度.md") in r.headers["content-disposition"]


def test_export_filename_encodes_slash_in_title(auth_client):
    """异常场景（用户可见）：标题含 `/` 时导出文件名必须是合法的 RFC 5987 头。

    触发路径**很现实**：会话默认标题取**提问前 20 字**（`TITLE_MAX_CHARS = 20`），
    而本项目最常见的提问开头就是「GB/T …」。
    `quote` 默认 `safe='/'` **不编码斜杠**，而 RFC 5987 的 attr-char 不含 `/` ——
    实测会发出 `filename*=UTF-8''GB/T50204.md` 这种畸形头，浏览器可能截断，
    也可能整个忽略 `filename*`（于是文件名退化成 URL 最后一段）。
    **把 `safe=""` 去掉，本用例即红。**
    """
    sid = S.create_session("GB/T50204 混凝土强度评定")
    S.append_message(sid, "user", "如何评定")
    cd = auth_client.get(f"/qa/sessions/{sid}/export").headers["content-disposition"]

    assert "filename*=UTF-8''" in cd
    encoded = cd.split("filename*=UTF-8''", 1)[1]
    assert "%2F" in cd, "标题里的 / 必须被百分号编码（quote 的默认 safe='/' 不会编码它）"
    assert "/" not in encoded, \
        f"filename* 的值里不得残留裸斜杠（RFC 5987 attr-char 不含 /）：{cd!r}"


def test_export_missing_session_returns_404(auth_client):
    """异常场景：导出不存在的会话返回 404。

    ⚠️ 同样必须断言 body——只断状态码时，路由缺失也会因 FastAPI 默认 404 而假通过。
    """
    r = auth_client.get("/qa/sessions/999999/export")
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在"


# ── 跨会话搜索接口（T12） ──

def test_search_endpoint_returns_hits(auth_client):
    """正常场景：命中返回消息 + 所属会话名 + 消息 id（前端用于定位跳转）。"""
    sid = S.create_session("混凝土")
    S.append_message(sid, "user", "混凝土强度等级如何评定")
    r = auth_client.get("/qa/search", params={"q": "混凝土"})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["session_id"] == sid
    assert hits[0]["session_title"] == "混凝土"
    assert "id" in hits[0]
    # 追加断言（brief 只断 id 存在、未断内容）：钉住返回体确实是"这条消息"，
    # 而非"某个恰好也被算作命中的东西"。注意本用例库里只有这一条消息，
    # 故"路由把 q 丢掉、直接返回整表"同样会得到 hits == 1 —— 该缺口由下一条
    # 用例 test_search_endpoint_filters_by_keyword 单独堵住。
    assert hits[0]["content"] == "混凝土强度等级如何评定"
    assert hits[0]["role"] == "user"


def test_search_endpoint_filters_by_keyword(auth_client):
    """正常场景：关键词确实参与过滤——跨会话只返回命中的会话，未命中的不出现。"""
    a = S.create_session("混凝土会话")
    S.append_message(a, "user", "混凝土强度等级如何评定")
    b = S.create_session("钢筋会话")
    S.append_message(b, "user", "钢筋锚固长度怎么算")
    hits = auth_client.get("/qa/search", params={"q": "混凝土"}).json()["hits"]
    # 忽略 q 时这里会拿到 2 条（两个会话各一条）→ 断言失败
    assert [h["session_id"] for h in hits] == [a]
    # 无命中 → 空数组（统一结构，禁止返回 null）
    assert auth_client.get("/qa/search", params={"q": "钢结构"}).json() == {"hits": []}


def test_search_endpoint_blank_query_returns_empty(auth_client):
    """边界场景：空查询返回空数组，不退化为全量。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "x")
    assert auth_client.get("/qa/search", params={"q": "  "}).json() == {"hits": []}
    # 追加断言：省略 q 时必须落到默认空串，而不是 422（默认值也是契约的一部分）
    assert auth_client.get("/qa/search").json() == {"hits": []}


def test_search_endpoint_escapes_wildcards(auth_client):
    """异常场景：% 被转义，不得命中全部消息。"""
    sid = S.create_session("s")
    S.append_message(sid, "user", "普通内容")
    assert auth_client.get("/qa/search", params={"q": "%"}).json() == {"hits": []}
    # 追加断言：转义不得"把 % 一律打死"——字面 % 仍应命中含它的那条
    S.append_message(sid, "assistant", "含水率 100% 的说明")
    hits = auth_client.get("/qa/search", params={"q": "100%"}).json()["hits"]
    assert [h["content"] for h in hits] == ["含水率 100% 的说明"]


def test_search_endpoint_escapes_underscore_wildcard(auth_client):
    """异常场景：_ 被单独转义——搜 "_" 只命中字面含下划线的消息，不得命中全部。

    与 % 分开断言：`_escape_like` 里两个 replace 是两行独立代码，只测 % 时
    删掉 `_` 那一行照样通过（`%` 用例覆盖不到它）。
    """
    sid = S.create_session("s")
    S.append_message(sid, "user", "普通内容")          # 不含下划线
    S.append_message(sid, "user", "字段 a_b 的说明")   # 含字面下划线
    hits = auth_client.get("/qa/search", params={"q": "_"}).json()["hits"]
    # 未转义时 "_" 退化为"任意单字符" → 两条都命中（len == 2）→ 本断言失败
    assert [h["content"] for h in hits] == ["字段 a_b 的说明"]


def test_search_endpoint_escapes_backslash(auth_client):
    """异常场景：反斜杠本身被转义（`_escape_like` 的第一步，前提是 `ESCAPE '\\'`）。

    为什么单独测：`\\` 是 ESCAPE 字符自身。它若不翻倍，搜索串尾部会与 `%`/`_` 前插入的
    转义反斜杠连成 dangling escape——轻则把尾部通配符吃掉、重则 SQL 报错。
    删掉 `_escape_like` 里 `text.replace("\\\\", "\\\\\\\\")` 那一行，本用例必须失败。
    """
    sid = S.create_session("s")
    S.append_message(sid, "user", r"C:\spec\gb50204 的路径写法")
    S.append_message(sid, "user", "普通内容")
    # 未转义时 "\" 会与后续插入的转义反斜杠串联，命中的集合与下面断言不同
    hits = auth_client.get("/qa/search", params={"q": "\\"}).json()["hits"]
    assert [h["content"] for h in hits] == [r"C:\spec\gb50204 的路径写法"]


def test_search_endpoint_caps_hits_at_single_call_limit(auth_client):
    """边界场景：命中数超过单次上限时截断到上限。

    只断"返回的是列表"覆盖不到上限——忽略上限（例如调用时传了个大 limit
    或把 search_messages 的默认 limit 去掉）时本用例会拿到 n 条而失败。
    """
    from app.database import get_db

    sid = S.create_session("s")
    n = S._SEARCH_LIMIT + 5
    # 刻意绕开 S.append_message：逐条 append 是循环内单次提交（违反铁律 1.3
    # 「批量操作必须走批量接口」），本处用 executemany 一次写入
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO qa_messages (session_id, role, content) VALUES (?,?,?)",
            [(sid, "user", f"混凝土 {i}") for i in range(n)],
        )
    hits = auth_client.get("/qa/search", params={"q": "混凝土"}).json()["hits"]
    assert len(hits) == S._SEARCH_LIMIT


def test_search_route_coexists_with_session_detail_route(auth_client):
    """路由不互相遮蔽：/qa/search 与 /qa/sessions/{id} 各自命中自己的处理器。

    /qa/search 是固定段、/qa/sessions/{session_id} 首段不同，理论上不冲突；
    此处用一条实测把"两条路由都活着且返回结构各不相同"钉住
    （任一被遮蔽时解析不出对应键 → 失败）。
    """
    sid = S.create_session("s")
    S.append_message(sid, "user", "混凝土")
    assert list(auth_client.get("/qa/search", params={"q": "混凝土"}).json()) == ["hits"]
    assert list(auth_client.get(f"/qa/sessions/{sid}").json()) == ["session", "messages"]
