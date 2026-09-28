"""Task 6：注册参数 search.breadcrumb_weight（检索分组，默认 0.3，范围 0~2）

帮助文案必须与实现语义一致：0 = 完全不参与（由 Task 5 的列限定 MATCH 实现），
**不是**「仅调弱排序」——实测 bm25 权重 0 关不掉 breadcrumb 列的召回。
"""
from app import config
from app.database import init_db
from app.params import registry


def test_breadcrumb_weight_is_registered():
    meta = {m["key"]: m for m in registry.PARAM_META}
    m = meta["search.breadcrumb_weight"]
    assert m["group"] == "search"
    assert m["type"] == "float"       # C-7：_num() 产出的键是 "type"，不是 "dtype"
    assert float(m["default"]) == 0.3
    assert float(m["min"]) == 0.0 and float(m["max"]) == 2.0
    # 帮助文案必须诚实：0 表示不参与（由列限定 MATCH 实现），而非「仅调弱排序」
    assert "0" in m["help"]
    assert "完全不参与" in m["help"]


def test_breadcrumb_weight_default_matches_config_constant():
    """注册表默认值取 config 常量（单一数据源，防两处漂移）"""
    assert float(config.SEARCH_BREADCRUMB_WEIGHT) == 0.3


def test_breadcrumb_weight_visible_in_search_group():
    """参数页从注册表渲染 → 键必须出现在 search 分组（紧随 search.lexicon_expand）"""
    keys = registry.keys_by_group("search")
    assert "search.breadcrumb_weight" in keys
    assert keys.index("search.breadcrumb_weight") == keys.index("search.lexicon_expand") + 1


def test_breadcrumb_weight_value_validation():
    """0 合法（等价于关闭面包屑）；超出 0~2 区间被拒"""
    assert registry.validate_value("search.breadcrumb_weight", "0") == (True, "")
    ok, err = registry.validate_value("search.breadcrumb_weight", "2.5")
    assert ok is False and err == "超出可调范围"


def test_breadcrumb_weight_present_in_params_meta_api(auth_client, monkeypatch, tmp_path):
    """参数页的数据源（GET /maintenance/params/meta）必须下发该键 —— 无需改 UI 即出现"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "bc_meta.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_bc_meta"))
    init_db()
    resp = auth_client.get("/maintenance/params/meta")
    assert resp.status_code == 200
    params = {p["key"]: p for p in resp.json()["params"]}
    m = params["search.breadcrumb_weight"]
    assert m["group"] == "search" and m["label"] == "面包屑权重"
