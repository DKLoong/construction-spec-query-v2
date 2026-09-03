"""参数注册表测试：元数据/默认/DB 覆盖/非法回退/clamp/自适应组装/缓存失效"""
import pytest
from app.config import QA_CONFIG_DEFAULTS
from app.database import init_db, get_db
from app.params import registry


def _setup(monkeypatch, tmp_path, name="p.db"):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / f"lance-{name}"))
    init_db()
    registry.clear_param_cache()
    return db_path


def _set_setting(key, value):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)))


def test_meta_covers_three_groups_and_fields():
    assert [g["id"] for g in registry.PARAM_GROUPS] == ["classify", "search", "qa"]
    keys = [m["key"] for m in registry.PARAM_META]
    assert "classify.ai_confidence_threshold" in keys
    assert "classify.threshold.dim1" in keys and "classify.threshold.dim6" in keys
    assert "search.vector_l2_threshold" in keys and "search.rrf_k" in keys
    assert "qa.rerank.min_score" in keys
    assert all(k.startswith(("classify.", "search.", "qa.")) for k in keys)
    for m in registry.PARAM_META:
        assert m["label"] and m["placeholder"] and m.get("help")
        assert m["type"] in ("int", "float", "str")
        if m["type"] in ("int", "float"):
            assert m["min"] is not None and m["max"] is not None


def test_defaults_when_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert registry.get_param_float("classify.ai_confidence_threshold") == pytest.approx(0.7)
    assert registry.get_param_int("classify.batch_size") == 20
    assert registry.get_param_int("search.vector_top_k") == 20
    assert registry.get_param_float("search.vector_l2_threshold") == pytest.approx(1.0)
    assert registry.get_param_int("search.rrf_k") == 60
    assert registry.get_param_float("qa.rerank.min_score") == pytest.approx(0.5)


def test_db_override_takes_effect_after_clear(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # 缓存命中：写入后未清缓存仍读默认
    assert registry.get_param_float("classify.ai_confidence_threshold") == pytest.approx(0.7)
    _set_setting("classify.ai_confidence_threshold", "0.85")
    assert registry.get_param_float("classify.ai_confidence_threshold") == pytest.approx(0.7)
    registry.clear_param_cache()
    assert registry.get_param_float("classify.ai_confidence_threshold") == pytest.approx(0.85)


def test_invalid_override_falls_back_to_default(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _set_setting("classify.ai_confidence_threshold", "abc")
    registry.clear_param_cache()
    assert registry.get_param_float("classify.ai_confidence_threshold") == pytest.approx(0.7)
    _set_setting("classify.batch_size", "notint")
    registry.clear_param_cache()
    assert registry.get_param_int("classify.batch_size") == 20


def test_numeric_getter_clamps_to_meta_range(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _set_setting("classify.batch_size", "99999")
    registry.clear_param_cache()
    assert registry.get_param_int("classify.batch_size") == 500  # 上限 clamp
    _set_setting("search.vector_l2_threshold", "5.0")
    registry.clear_param_cache()
    assert registry.get_param_float("search.vector_l2_threshold") == pytest.approx(2.0)


def test_adaptive_thresholds_assembled_from_db(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _set_setting("classify.threshold.dim5", "0.9")
    registry.clear_param_cache()
    d = registry.get_adaptive_thresholds()
    assert set(d) == {"dim1", "dim2", "dim3", "dim4", "dim5", "dim6"}
    assert d["dim5"] == pytest.approx(0.9)
    assert d["dim1"] == pytest.approx(0.3)  # 未覆盖维保持内置默认


def test_qa_numeric_defaults_match_qa_config_defaults(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for m in registry.PARAM_META:
        if m["group"] == "qa" and m["type"] != "str":
            sub = m["key"][len("qa."):]
            assert m["default"] == float(QA_CONFIG_DEFAULTS[sub]), m["key"]


def test_validate_value_range_and_type(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ok, msg = registry.validate_value("classify.ai_confidence_threshold", "0.75")
    assert ok
    ok, msg = registry.validate_value("classify.ai_confidence_threshold", "1.5")
    assert not ok
    ok, msg = registry.validate_value("classify.batch_size", "0")
    assert not ok
    ok, _ = registry.validate_value("no.such.key", "1")
    assert not ok
