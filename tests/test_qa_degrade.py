"""精排降级：级别建模、排名归一化分数、阈值解析。"""
import pytest

from app.qa.degrade import (
    RERANK_CE, RERANK_VECTOR, RERANK_NONE,
    rank_scores, resolve_thresholds,
)


def test_rank_scores_is_descending_and_within_unit_interval():
    """正常场景：分数降序、落在 [0, 1)，首项最大。"""
    scores = rank_scores(10)
    assert len(scores) == 10
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s < 1.0 for s in scores)


def test_rank_scores_boundaries():
    """边界场景：n=0 返回空；n=1 返回单元素。"""
    assert rank_scores(0) == []
    assert len(rank_scores(1)) == 1


def test_rank_scores_never_all_one():
    """异常场景（回归守卫）：第 3 级降级不得再把分数拍平成全 1.0。

    全 1.0 会让 tier_items 把全部候选判为 high（强相关），
    导致全部全文进上下文、摘要压缩失效、token 预算被吃光。
    """
    scores = rank_scores(30)
    assert len(set(scores)) > 1, "分数必须能区分次序，不能全相同"
    assert max(scores) < 1.0


def test_rank_scores_splits_top_third_as_high():
    """正常场景：配合 high_threshold=2/3 时，恰好前 1/3 落入 high 区。"""
    n = 30
    scores = rank_scores(n)
    high_thr = 2.0 / 3.0
    high_count = sum(1 for s in scores if s >= high_thr)
    assert high_count == n // 3


def test_resolve_thresholds_ce_uses_rerank_set():
    """正常场景：CE 可用时用 qa.rerank.* 阈值集。"""
    fake = {"rerank.min_score": 0.50, "rerank.high_threshold": 0.80}
    min_score, high_thr = resolve_thresholds(RERANK_CE, fake.__getitem__)
    assert (min_score, high_thr) == (0.50, 0.80)


def test_resolve_thresholds_vector_uses_vector_set():
    """正常场景：向量降级时用 qa.vector.* 阈值集。"""
    fake = {"vector.min_score": 0.30, "vector.high_threshold": 0.55}
    min_score, high_thr = resolve_thresholds(RERANK_VECTOR, fake.__getitem__)
    assert (min_score, high_thr) == (0.30, 0.55)


def test_resolve_thresholds_none_disables_absolute_threshold():
    """异常场景：第 3 级降级不得沿用向量阈值。

    RRF 排名无绝对相关度语义，沿用 0.30/0.55 会误杀或误判，
    必须改为 min_score=0（不丢条）+ high_threshold=2/3（按排名切强弱）。
    """
    min_score, high_thr = resolve_thresholds(RERANK_NONE, lambda k: 0.30)
    assert min_score == 0.0
    assert high_thr == pytest.approx(2.0 / 3.0)


# ── 第 3 级降级在 _rerank_scored 真实路径上的行为（含保序） ──


def _raise_rerank(question, texts):
    raise RuntimeError("CrossEncoder 不可用")


def _raise_embed(texts):
    raise RuntimeError("embedding 不可用")


def test_rerank_scored_none_branch_uses_rank_scores(monkeypatch):
    """回归守卫：CE 与向量皆不可用时，_rerank_scored 必须返回排名归一化分数。

    这是本 Task 的唯一行为交付物。若把返回值改回 [(c, 1.0) ...]（即重现将全部候选
    判为强相关的原 bug），本用例会因 max(scores) < 1.0 与「严格递减」两条断言而失败。
    """
    from app.routes import qa_routes

    candidates = [
        {"spec_code": "GB 50204", "clause_no": str(i),
         "content": f"条文内容{i}，用于第 3 级降级排序测试。"}
        for i in range(6)
    ]
    # 两条降级腿都打断 → 落到第 3 级
    monkeypatch.setattr("app.ai.reranker.rerank", _raise_rerank)
    monkeypatch.setattr("app.ai.embedding.embed_texts", _raise_embed)

    ranked = qa_routes._rerank_scored("模板设计要求", candidates)

    assert qa_routes._last_rerank_used == RERANK_NONE

    scores = [s for _, s in ranked]
    assert len(ranked) == len(candidates), "第 3 级不得丢条"
    # 不再拍平成全 1.0：必须能区分次序，且不得触及上界 1.0
    assert max(scores) < 1.0
    assert all(0.0 <= s < 1.0 for s in scores)
    # 严格递减（rank_scores 对 n 条给出互不相同的值）
    assert len(set(scores)) == len(scores)
    assert scores == sorted(scores, reverse=True)

    # 保序：第 3 级不 sorted()，名次即相对相关度 → 候选顺序必须与输入一致
    assert [c["clause_no"] for c, _ in ranked] == [c["clause_no"] for c in candidates]


def test_rerank_scored_none_branch_order_follows_input(monkeypatch):
    """保序语义：上游给什么顺序，第 3 级就按什么顺序赋名次分。

    candidates 已按 RRF 降序（见 qa_routes.py 的 hybrid_search → filter_by_metadata
    链路，后者为保序过滤），故首位必须拿到最高分。
    """
    from app.routes import qa_routes

    # 故意用「非字典序」的顺序，避免用例被巧合的排序掩盖
    order = ["9", "1", "7", "3", "5"]
    candidates = [
        {"spec_code": "GB 50204", "clause_no": no, "content": f"条文{no}"}
        for no in order
    ]
    monkeypatch.setattr("app.ai.reranker.rerank", _raise_rerank)
    monkeypatch.setattr("app.ai.embedding.embed_texts", _raise_embed)

    ranked = qa_routes._rerank_scored("模板设计要求", candidates)

    assert qa_routes._last_rerank_used == RERANK_NONE
    assert [c["clause_no"] for c, _ in ranked] == order
    # 首位最高分，末位最低分（名次分随输入次序单调下降）
    scores = [s for _, s in ranked]
    assert scores[0] == max(scores)
    assert scores[-1] == min(scores)
