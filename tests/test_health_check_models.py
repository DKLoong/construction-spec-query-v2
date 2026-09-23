# tests/test_health_check_models.py
"""健康检查：AI 模型就绪项（分享场景下用于告知缺什么）。"""
from unittest.mock import patch

from app.ai import embedding as embedding_mod
from app.ai import reranker as reranker_mod
from app.maintenance.health_check import run_health_check


def test_model_ready_ok_when_both_models_available(qa_db):
    """正常场景：两个模型都在 → ok，且无 hint。"""
    with patch("app.ai.reranker.is_ready", return_value=True), \
         patch("app.ai.embedding.is_ready", return_value=True):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "ok"
    assert item["count"] == 0
    # 状态列留空：模板对 severity=="ok" 且 status_text 为空的行渲染「✅ 正常」，
    # 故此处断言空串即可钉住「未误挂缺失文案」。
    assert item["status_text"] == ""


def test_model_ready_warns_when_reranker_missing(qa_db):
    """边界场景：仅缺 CrossEncoder → warn，提示精排降级。"""
    with patch("app.ai.reranker.is_ready", return_value=False), \
         patch("app.ai.embedding.is_ready", return_value=True):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "warn"
    assert "精排" in item["hint"]
    # 状态列不得是通用的「⚠️ 可修复」：缺模型文件只能由用户放进 models/BAAI/，
    # 系统无法代劳——宣称"可修复"会直接误导分享场景下的使用者。
    assert item["status_text"] == "⚠️ 功能降级"
    assert "可修复" not in item["status_text"]
    assert item["fixable"] is False


def test_health_check_does_not_instantiate_models(qa_db):
    """异常场景（副作用守卫）：健康检查不得触发模型加载。

    get_reranker()/get_model() 在未加载时会真实例化模型（数秒），而本检查
    在维护页每次打开都跑。用「调用即抛」的桩钉住这一点。
    """
    def _boom(*a, **k):
        raise AssertionError("健康检查触发了模型实例化")

    with patch("app.ai.reranker.get_reranker", _boom), \
         patch("app.ai.embedding.get_model", _boom), \
         patch("app.ai.reranker.is_ready", return_value=True), \
         patch("app.ai.embedding.is_ready", return_value=True):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "ok"


def test_model_ready_errors_when_embedding_missing(qa_db):
    """异常场景：缺 embedding → 向量召回一并失效，严重度高于仅缺精排。"""
    with patch("app.ai.reranker.is_ready", return_value=False), \
         patch("app.ai.embedding.is_ready", return_value=False):
        result = run_health_check()
    item = next(c for c in result["checks"] if c["key"] == "model_ready")
    assert item["severity"] == "error"
    assert "向量召回" in item["hint"]
    # 同 warn 档：须为需人工处理的显式文案，而非系统代劳不了的「可修复」。
    assert item["status_text"] == "⛔ 需人工处理"
    assert "可修复" not in item["status_text"]
    assert item["fixable"] is False


# ---------------------------------------------------------------- is_ready()
# 探测函数自身的行为：三态哨兵 + 本地目录探测，且**任何分支都不得触发加载**。

def _boom(*a, **k):
    raise AssertionError("is_ready() 触发了模型实例化")


def test_reranker_is_ready_reflects_loaded_and_failed_state(monkeypatch):
    """已加载实例 → True；曾尝试且失败（False 哨兵）→ False。"""
    monkeypatch.setattr(reranker_mod, "_model", object())
    assert reranker_mod.is_ready() is True
    monkeypatch.setattr(reranker_mod, "_model", False)
    assert reranker_mod.is_ready() is False


def test_reranker_is_ready_probes_local_dir_without_loading(monkeypatch, tmp_path):
    """未尝试加载 → 只看本地目录，且绝不调用 get_reranker()。"""
    local = tmp_path / "bge-reranker-base"
    local.mkdir()
    (local / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(reranker_mod, "_LOCAL_MODEL_PATHS", [local])
    monkeypatch.setattr(reranker_mod, "_model", None)
    with patch("app.ai.reranker.get_reranker", _boom):
        assert reranker_mod.is_ready() is True
        monkeypatch.setattr(reranker_mod, "_LOCAL_MODEL_PATHS", [tmp_path / "empty"])
        assert reranker_mod.is_ready() is False


def test_embedding_is_ready_reflects_loaded_and_failed_state(monkeypatch):
    """已加载实例 → True；曾尝试且失败（False 哨兵）→ False。"""
    monkeypatch.setattr(embedding_mod, "_model", object())
    assert embedding_mod.is_ready() is True
    monkeypatch.setattr(embedding_mod, "_model", False)
    assert embedding_mod.is_ready() is False


def test_embedding_is_ready_probes_local_dir_without_loading(monkeypatch, tmp_path):
    """未尝试加载 → 只看本地目录，且绝不调用 get_model()。"""
    local = tmp_path / "bge-small-zh-v1.5"
    local.mkdir()
    (local / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(embedding_mod, "_LOCAL_MODEL_PATHS", [local])
    monkeypatch.setattr(embedding_mod, "_model", None)
    with patch("app.ai.embedding.get_model", _boom):
        assert embedding_mod.is_ready() is True
        monkeypatch.setattr(embedding_mod, "_LOCAL_MODEL_PATHS", [tmp_path / "empty"])
        assert embedding_mod.is_ready() is False
