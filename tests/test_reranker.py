"""CrossEncoder 精排（reranker）单元测试

硬性约束：绝不真实加载模型、绝不触发网络下载 —— 全部用 monkeypatch 伪造。
"""
import pytest


def test_rerank_returns_scores_aligned_with_texts(monkeypatch):
    """模型可用时返回与 texts 对齐的分数列表（验证 pairs 构造 + tolist）"""
    import numpy as np
    from app.ai import reranker

    seen_pairs = {}

    class _FakeCrossEncoder:
        def predict(self, pairs):
            seen_pairs["pairs"] = pairs
            # 模拟真实 predict 返回 numpy 数组
            return np.array([0.9, 0.5, 0.8])

    monkeypatch.setattr(reranker, "_model", _FakeCrossEncoder())

    scores = reranker.rerank("模板设计要求", ["条文甲", "条文乙", "条文丙"])

    assert isinstance(scores, list)
    assert len(scores) == 3
    assert scores == [0.9, 0.5, 0.8]
    assert all(isinstance(s, float) for s in scores)
    # 验证构造的 (question, text) 对与输入对齐
    assert seen_pairs["pairs"] == [
        ("模板设计要求", "条文甲"),
        ("模板设计要求", "条文乙"),
        ("模板设计要求", "条文丙"),
    ]


def test_rerank_empty_texts_returns_empty():
    """空 texts 返回 []，且不触发模型加载"""
    from app.ai import reranker
    assert reranker.rerank("问题", []) == []


def test_rerank_model_unavailable_returns_none(monkeypatch):
    """模型不可用（_model=False）时 rerank 返回 None"""
    from app.ai import reranker
    monkeypatch.setattr(reranker, "_model", False)
    assert reranker.rerank("问题", ["条文"]) is None


def test_get_reranker_returns_none_when_load_failed(monkeypatch):
    """get_reranker 加载失败（_model=False 表示失败态）返回 None"""
    from app.ai import reranker
    monkeypatch.setattr(reranker, "_model", False)
    assert reranker.get_reranker() is None


def test_get_reranker_returns_none_when_constructor_raises(monkeypatch):
    """CrossEncoder 构造抛出异常时 get_reranker 吞异常并返回 None"""
    import sentence_transformers
    from app.ai import reranker

    def _raise(*args, **kwargs):
        raise RuntimeError("模拟模型加载失败")

    monkeypatch.setattr(reranker, "_model", None)  # 强制进入加载分支
    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _raise)

    assert reranker.get_reranker() is None


def test_get_reranker_prefers_local_path(monkeypatch, tmp_path):
    """本地模型目录存在（含 config.json）时优先从本地加载"""
    import sentence_transformers
    from app.ai import reranker

    local_dir = tmp_path / "BAAI" / "bge-reranker-base"
    local_dir.mkdir(parents=True)
    (local_dir / "config.json").write_text("{}", encoding="utf-8")

    captured = {}

    class _FakeCrossEncoder:
        def __init__(self, model_path, **kwargs):
            captured["path"] = model_path
            captured["kwargs"] = kwargs

    monkeypatch.setattr(reranker, "_model", None)
    monkeypatch.setattr(reranker, "_LOCAL_MODEL_PATHS", [local_dir])
    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _FakeCrossEncoder)

    model = reranker.get_reranker()

    assert model is not None
    assert captured["path"] == str(local_dir)
    # 未传 local_files_only（本地路径分支不要求该参数）
    assert "local_files_only" not in captured["kwargs"]


def test_get_reranker_falls_back_to_hf_cache(monkeypatch, tmp_path):
    """本地模型目录不存在时回退 HuggingFace 缓存（local_files_only=True）"""
    import sentence_transformers
    from app.ai import reranker

    empty_dir = tmp_path / "BAAI" / "bge-reranker-base"
    empty_dir.mkdir(parents=True)  # 目录存在但无 config.json

    captured = {}

    class _FakeCrossEncoder:
        def __init__(self, model_path, **kwargs):
            captured["path"] = model_path
            captured["kwargs"] = kwargs

    monkeypatch.setattr(reranker, "_model", None)
    monkeypatch.setattr(reranker, "_LOCAL_MODEL_PATHS", [empty_dir])
    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _FakeCrossEncoder)

    model = reranker.get_reranker()

    assert model is not None
    assert captured["path"] == "BAAI/bge-reranker-base"
    assert captured["kwargs"].get("local_files_only") is True
