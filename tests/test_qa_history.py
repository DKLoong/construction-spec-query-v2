"""多轮历史段组装（精简多轮：只带问答文本，绝不复用历史条文）。"""
from app.qa.context import build_history, HISTORY_HEADER


def _msgs(*pairs):
    out = []
    for q, a in pairs:
        out.append({"role": "user", "content": q})
        out.append({"role": "assistant", "content": a})
    return out


def test_build_history_empty_when_no_messages():
    """边界场景：无消息 → 空串（纯单轮语义）。"""
    assert build_history([], 6, 6000) == ""


def test_build_history_zero_turns_disables_history():
    """边界场景：max_turns=0 → 关闭历史，等同单轮。"""
    assert build_history(_msgs(("q1", "a1")), 0, 6000) == ""


def test_build_history_keeps_only_recent_turns():
    """正常场景：只保留最近 N 轮，更早的被丢弃。"""
    msgs = _msgs(("老问题", "老答案"), ("新问题", "新答案"))
    out = build_history(msgs, 1, 6000)
    assert "新问题" in out and "新答案" in out
    assert "老问题" not in out


def test_build_history_includes_q_and_a():
    """正常场景：历史段含问与答。"""
    out = build_history(_msgs(("混凝土强度", "按 GB 50204 评定")), 6, 6000)
    assert "混凝土强度" in out and "按 GB 50204 评定" in out
    assert out.startswith(HISTORY_HEADER)


def test_build_history_never_contains_sources():
    """异常场景（核心不变量）：历史段绝不能带历史条文上下文。

    条文每轮由 hybrid_search 重新召回；把历史条文带进上下文会导致
    token 平方级增长，且旧条文可能与新问题矛盾。
    """
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "a",
             "sources": [{"code": "GB 50204", "clause_no": "8.2.1"}]}]
    out = build_history(msgs, 6, 6000)
    assert "8.2.1" not in out


def test_build_history_drops_oldest_when_over_budget():
    """边界场景：历史段超预算时逐轮丢弃最旧，保留最近的。"""
    long_a = "很长" * 400          # 约 800 字符 ≈ 400 token
    msgs = _msgs(("旧问题", long_a), ("旧问题2", long_a), ("新问题", "短答案"))
    out = build_history(msgs, 6, token_budget=300)   # 预算不足以装下多轮
    assert "新问题" in out
    assert out.count("很长") < 2 * 400 / 2   # 未把全部长答案都塞进去


def test_build_history_does_not_truncate_inside_an_answer():
    """边界场景：不得截断单条答案内部（与 build_context 的既有原则一致）。"""
    answer = "起" + "中" * 100 + "止"
    out = build_history([{"role": "user", "content": "q"},
                         {"role": "assistant", "content": answer}], 6, 6000)
    assert "止" in out, "单条答案必须完整，不得从中间截断"


def test_build_history_skips_malformed_entries():
    """异常场景：缺 role/content 的脏数据被跳过，不抛异常。

    注（与本 Task brief 的唯一偏差）：brief 原用例末尾只有一条孤立 user
    （无答案），按 `_turns` 的定义「末尾孤立的 user 不构成轮次」→ 无轮次 →
    必然返回空串，原断言必然失败。这是 brief 用例的笔误（其实现侧的配对
    语义见 `_turns` docstring 与设计文档 §4.5「取最近 N 轮『问+答』」）。
    此处补上配对的 assistant 答案，并使用有区分度的脏数据内容，使断言真正
    覆盖「脏数据被跳过」这一意图（原 `"x"` 为单字，无法据以断言未泄漏）。
    """
    out = build_history([{"role": "user"}, {"content": "脏数据"},
                         None, {"role": "user", "content": "有效"},
                         {"role": "assistant", "content": "有效答案"}], 6, 6000)
    assert "有效" in out and "有效答案" in out
    assert "脏数据" not in out


def test_build_history_ignores_unanswered_trailing_user():
    """边界场景：末尾孤立 user（已提问但无答案，如请求失败）不构成轮次。

    这是 `_turns` 的既有语义（brief Step 3 docstring：末尾孤立的 user 不构成
    轮次），本用例把它钉住——上下文的组装方只在落库本轮消息**之前**读历史，
    因此「当前提问」不会被当成历史重复注入。
    """
    msgs = [{"role": "user", "content": "老问题"},
            {"role": "assistant", "content": "老答案"},
            {"role": "user", "content": "悬空新问题"}]
    out = build_history(msgs, 6, 6000)
    assert "老答案" in out
    assert "悬空新问题" not in out
