"""检索/问答共用精排降级链（app/search/rerank.py）单元测试

硬性约束：绝不真实加载模型、绝不触发网络下载 —— 全部用 monkeypatch 伪造。
"""


def _make_candidates(n):
    """构造 n 条候选条文（与 QA 精排测试同构）"""
    return [
        {"spec_code": "GB 50204", "clause_no": str(i),
         "content": f"条文内容{i}，用于精排打分排序测试。"}
        for i in range(n)
    ]


def test_rerank_candidates_strips_markup_before_scoring(monkeypatch):
    """喂给 CrossEncoder 的文本必须先去掉 OCR 标记/LaTeX

    精排只取前 300 字符，而表格条文的前 300 字符几乎全是 HTML——
    不清洗等于把「接头极限抗拉强度」这类正文截掉、只剩标记给模型打分。
    """
    from app.search.rerank import rerank_candidates

    candidates = [
        {"spec_code": "JGJ 107", "clause_no": "3.0.5",
         "content": ('<div style="text-align: center;">表3.0.5 接头极限抗拉强度</div>'
                     '<table border=1><tr><td style="text-align: center;">接头等级</td>'
                     '<td colspan="2">Ⅰ级</td></tr></table>'
                     "抗拉强度 $ N/mm^{{2}} $ 应符合表3.0.5的规定。")},
        {"spec_code": "JGJ 107", "clause_no": "3.0.7", "content": "接头变形性能应符合规定。"},
    ]
    captured = []

    def fake_rerank(question, texts):
        captured.extend(texts)
        return [0.5, 0.5]

    monkeypatch.setattr("app.ai.reranker.rerank", fake_rerank)

    rerank_candidates("接头抗拉强度要求", candidates)

    assert len(captured) == 2
    scored = captured[0]
    assert "<" not in scored and "$" not in scored
    for bad in ("div", "style", "table", "border", "td", "tr", "colspan", "N/mm"):
        assert bad not in scored, f"精排文本残留标记: {bad}"
    assert "接头极限抗拉强度" in scored
    assert "接头等级" in scored
    assert "应符合表3.0.5的规定" in scored
    # 无标记候选逐字不变
    assert captured[1] == "接头变形性能应符合规定。"


def test_rerank_candidates_crossencoder_desc(monkeypatch):
    """CrossEncoder 可用时返回 (候选, 分数) 按分数降序全量 + 档位 crossencoder"""
    from app.search.rerank import rerank_candidates

    candidates = _make_candidates(8)
    scores = [0.1, 0.9, 0.3, 0.8, 0.2, 0.7, 0.4, 0.5]

    def fake_rerank(question, texts):
        assert question == "模板设计要求"
        assert len(texts) == 8
        return scores

    monkeypatch.setattr("app.ai.reranker.rerank", fake_rerank)

    ranked, used = rerank_candidates("模板设计要求", candidates)

    assert used == "crossencoder"
    # 全量返回、按分数降序（条数截断在调用方编排层决定）
    assert [c["clause_no"] for c, _ in ranked] == ["1", "3", "5", "7", "6", "2", "4", "0"]
    assert [s for _, s in ranked] == sorted(scores, reverse=True)


def test_rerank_candidates_falls_back_to_vector(monkeypatch):
    """CrossEncoder 不可用（返回 None）时回退 bi-encoder 向量"""
    from app.search.rerank import rerank_candidates

    candidates = _make_candidates(3)
    monkeypatch.setattr("app.ai.reranker.rerank", lambda q, t: None)
    # q=[1,1]，条文 i 向量=[i+1,1] → dot=[2,3,4] → 降序为 2,1,0
    monkeypatch.setattr(
        "app.ai.embedding.embed_texts",
        lambda texts: [[float(i + 1), 1.0] for i in range(len(texts))],
    )

    ranked, used = rerank_candidates("模板设计要求", candidates)

    assert used == "vector"
    assert [c["clause_no"] for c, _ in ranked] == ["2", "1", "0"]


def test_rerank_candidates_short_circuit_single(monkeypatch):
    """候选 ≤1 时直接返回原序，不打分、不分层"""
    from app.search.rerank import rerank_candidates

    candidates = _make_candidates(1)
    called = {"rerank": False}

    def fake_rerank(q, t):
        called["rerank"] = True
        return [1.0] * len(t)

    monkeypatch.setattr("app.ai.reranker.rerank", fake_rerank)

    ranked, used = rerank_candidates("模板设计要求", candidates)

    assert ranked == [(candidates[0], 1.0)]
    assert not called["rerank"]
    assert used == "none"


def test_rerank_candidates_exception_falls_back_to_vector(monkeypatch):
    """CrossEncoder 精排抛异常时回退 bi-encoder 向量"""
    from app.search.rerank import rerank_candidates

    candidates = _make_candidates(3)
    monkeypatch.setattr(
        "app.ai.reranker.rerank",
        lambda q, t: (_ for _ in ()).throw(RuntimeError("精排异常")),
    )
    monkeypatch.setattr(
        "app.ai.embedding.embed_texts",
        lambda texts: [[1.0, 0.0] for _ in texts],
    )

    ranked, used = rerank_candidates("模板设计要求", candidates)

    assert used == "vector"
    assert len(ranked) == 3


def test_rerank_candidates_both_fail_keeps_original_order(monkeypatch):
    """CrossEncoder 与 bi-encoder 均失败时保持原始顺序 + 档位 none"""
    from app.search.rerank import rerank_candidates

    candidates = _make_candidates(3)

    def _raise_rerank(q, t):
        raise RuntimeError("精排异常")

    monkeypatch.setattr("app.ai.reranker.rerank", _raise_rerank)
    monkeypatch.setattr(
        "app.ai.embedding.embed_texts",
        lambda texts: (_ for _ in ()).throw(RuntimeError("向量失败")),
    )

    ranked, used = rerank_candidates("模板设计要求", candidates)

    assert used == "none"
    assert [c["clause_no"] for c, _ in ranked] == ["0", "1", "2"]
    assert all(s == 1.0 for _, s in ranked)
