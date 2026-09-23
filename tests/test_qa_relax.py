"""分类筛选候选不足：取消静默放宽，改显式提示 + 一键放宽。"""
from unittest.mock import AsyncMock

import pytest

from app.ai.cli_client import CLIResponse

_CAND = {"id": 1, "spec_code": "GB 50204", "clause_no": "8.2.1",
         "content": "混凝土", "title": "", "spec_status": "现行"}


def _backend(answer="答案"):
    b = AsyncMock()
    b.is_available = lambda: True
    # command 必须是**真字符串**：AsyncMock 的子属性本身也是 AsyncMock，
    # 而 /qa/ask 会读 backend.command 并做 .replace/.rsplit（见 qa_routes.py:344-347），
    # 让它自动生成会让整条链路在第 8 步就抛 AttributeError，测试根本走不到断言。
    b.command = "cli"
    b.ask = AsyncMock(return_value=CLIResponse(success=True, content=answer))
    return b


@pytest.fixture()
def qa_env(monkeypatch):
    """默认打桩：检索稳定返回 1 条候选、后端稳定成功。

    个别用例会用 monkeypatch 覆盖 hybrid_search 来模拟「筛选后候选不足」。
    """
    monkeypatch.setattr("app.search.hybrid_search.hybrid_search",
                        lambda sq: ([dict(_CAND)], 1))
    # 精排**恒等透传**（不是无视入参返回常量）：这样「哪一组候选流进了回答」
    # 在断言层可见——本 Task 的核心行为变更（宽检索结果不再被拿去回答）必须可证伪。
    monkeypatch.setattr("app.routes.qa_routes._rerank_scored",
                        lambda q, c: [(x, 0.9) for x in c])
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: _backend())


def test_narrow_filter_reports_filtered_out(auth_client, qa_env, monkeypatch):
    """正常场景：分类筛选候选不足时返回全局命中数，供前端提示。

    第 1 次 hybrid_search（带分类）返回 1 条 < qa_min_candidates(3)，
    触发诊断性的第 2 次（不带分类）→ 20 条，即 filtered_out。
    """
    calls = {"n": 0}

    def fake_search(sq):
        calls["n"] += 1
        if calls["n"] == 1:
            return [dict(_CAND)], 1
        return [dict(_CAND) for _ in range(20)], 20

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)

    r = auth_client.post("/qa/ask",
                         json={"question": "q", "dim4_specialty": ["混凝土"]})
    body = r.json()
    assert body["filtered_out"] == 20, "必须报告全局候选量，供前端提示可放宽"
    assert body["effective_filters"]["dim4_specialty"] == ["混凝土"]


def test_relaxed_request_skips_dimension_filters(auth_client, qa_env, monkeypatch):
    """正常场景：relaxed=True 时不携带分类维度检索，且不再报 filtered_out。"""
    seen = {}

    def fake_search(sq):
        seen["dims"] = sq.dim4_specialty
        return [dict(_CAND)], 1

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)

    body = auth_client.post("/qa/ask",
                            json={"question": "q", "dim4_specialty": ["混凝土"],
                                  "relaxed": True}).json()
    assert not seen["dims"], "relaxed 时必须清空分类维度"
    assert body["filtered_out"] == 0
    # 注意措辞：放宽清空的是**分类维度**，状态过滤仍生效（relaxed + status_filter
    # 会得到 {"status_filter": ...}）。本用例未带状态过滤，故为空。
    assert body["effective_filters"] == {}, "放宽清空分类维度，且本用例未设状态过滤"


def test_no_filter_no_relax_hint(auth_client, qa_env):
    """边界场景：未使用分类筛选时 filtered_out 为 0，不产生放宽提示。"""
    body = auth_client.post("/qa/ask", json={"question": "q"}).json()
    assert body["filtered_out"] == 0
    assert body["effective_filters"] == {}


def test_effective_filters_records_explicit_status(auth_client, qa_env):
    """正常场景：**显式**状态过滤要记入 effective_filters（前端「本轮生效」要用）。

    设计文档 §4.7 给该字段的定义是「分类维度 + 状态 + 前言放行」——上面三条用例
    只覆盖了「分类维度」这一半，状态那一半若无人断言，就是「测试锁不住自己名字里
    承诺的行为」（本项目复发率最高的缺陷类）。删掉实现里的 status_filter 两行，
    本条必须失败。
    """
    body = auth_client.post("/qa/ask",
                            json={"question": "q", "status_filter": "现行"}).json()
    assert body["effective_filters"]["status_filter"] == "现行"


def test_effective_filters_omits_default_status(auth_client, qa_env):
    """边界场景：status_filter 缺参（None）时**不记录**，避免把默认白名单误当用户显式选择。

    缺参语义是「旧客户端没带」→ 走 settings 默认白名单（见 `_prepare_qa_context` 的三分支）。
    把默认值记进「用户设了哪些筛选」会让前端常驻显示一行用户从未选择的筛选。
    注意：显式空串 `""`（全不勾 → 放行非现行）**是**用户选择，必须记录——故实现判的是
    `is not None` 而不是真值，本条与上一条一起把这两种语义钉开。
    """
    body = auth_client.post("/qa/ask", json={"question": "q"}).json()
    assert "status_filter" not in body["effective_filters"]
    # 显式空串是另一种语义：记录，且值为 ""
    body = auth_client.post("/qa/ask",
                            json={"question": "q", "status_filter": ""}).json()
    assert body["effective_filters"]["status_filter"] == ""


def test_wide_candidates_are_not_used_to_answer(auth_client, qa_env, monkeypatch):
    """核心行为（取消静默放宽）：候选不足时**只报告**全局命中数，**不得**把宽检索结果拿去回答。

    这是本 Task 唯一的行为变更，也是最容易被后来人打回退的地方。
    证伪方式：实现若仍写 `candidates, _ = hybrid_search(wide_sq)`（原地放宽），
    第二次检索的那条文就会流进上下文 → 下面的否定断言失败。
    **并且同时断言窄候选的内容确实在上下文里**（正向对照）——否则「上下文为空」
    也会让否定断言通过，那是另一种假通过（本 Task 的文件里已经出现过两次同类教训）。
    """
    captured = {}

    async def fake_ask(**kw):
        captured.update(kw)
        return CLIResponse(success=True, content="答案")

    b = AsyncMock()
    b.is_available = lambda: True
    b.command = "cli"   # 同上：必须是真字符串，否则走不到 prompt 组装
    b.ask = fake_ask
    monkeypatch.setattr("app.ai.cli_client.get_backend", lambda name=None: b)

    calls = {"n": 0}

    def fake_search(sq):
        calls["n"] += 1
        if calls["n"] == 1:
            return [dict(_CAND)], 1                        # 窄：content = "混凝土"
        return [{**_CAND, "content": "WIDE-ONLY-MARKER"}], 20

    monkeypatch.setattr("app.search.hybrid_search.hybrid_search", fake_search)

    body = auth_client.post(
        "/qa/ask", json={"question": "q", "dim4_specialty": ["混凝土"]}).json()

    assert body["filtered_out"] == 20, "必须如实报告全局命中数"
    ctx = captured.get("context", "")
    assert "混凝土" in ctx, "正向对照：窄候选确实进了上下文（否则下面一条是空断言）"
    assert "WIDE-ONLY-MARKER" not in ctx, "宽检索结果不得进入答案（静默放宽已取消）"


def test_qa_dim_fields_matches_request_model():
    """一致性守卫：QA_DIM_FIELDS 必须与 QaRequest 的维度字段完全对应。

    该常量是「检索条件构造」与「当轮筛选记录」的唯一来源。若将来新增维度
    只改了 QaRequest 而漏改常量，检索会**静默忽略新维度**（筛选界面能选、
    但不生效），极难排查。本用例把两者钉死，让漏改立刻失败。
    """
    from app.models import QA_DIM_FIELDS, QaRequest
    model_dims = {k for k in QaRequest.model_fields if k.startswith("dim")}
    assert set(QA_DIM_FIELDS) == model_dims, (
        f"QA_DIM_FIELDS 与 QaRequest 维度字段不一致："
        f"仅常量有 {set(QA_DIM_FIELDS) - model_dims}，仅模型有 {model_dims - set(QA_DIM_FIELDS)}"
    )
