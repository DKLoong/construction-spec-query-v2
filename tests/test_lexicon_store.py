"""Task 2 词库 store 单测：模块级 TTL 缓存命中、active 过滤、DB 真实加载、equiv 词表一致性。"""
import time

from app.database import DATABASE_PATH
from app.lexicon import store
from app.lexicon.store import (
    LexiconRow,
    invalidate_lexicon_caches,
    load_confusable_pairs,
    load_equivalent_groups,
)


def _mkrows():
    """注入假缓存并强制命中（_cache_path 对齐 + 时间戳前移）。"""
    store._cache = [
        LexiconRow(1, "alias", "混凝土", ["砼", "混泥土"], "", 1),
        LexiconRow(2, "synonym", "坍落度", ["塌落度"], "", 1),
        LexiconRow(3, "confusable", "箍筋", ["钢筋"], "子类区分", 1),
        LexiconRow(4, "alias", "I", ["1"], "", 0),   # 停用
    ]
    store._cache_ts = time.monotonic() + 1000  # 强制命中
    store._cache_path = DATABASE_PATH          # 命中需路径一致（TTL 守卫）


def test_equiv_filter_and_active():
    """缓存命中时：load_equivalent_groups 只返回 active 的 synonym/alias 组，
    confusable 独立返回、绝不混入等价组。"""
    _mkrows()
    eq = load_equivalent_groups()
    assert {g.canonical for g in eq} == {"混凝土", "坍落度"}
    assert {g.kind for g in eq} <= set(store.EQUIV_KINDS)
    cf = load_confusable_pairs()
    assert [c.canonical for c in cf] == ["箍筋"]


def test_store_loads_from_db(monkeypatch, tmp_path):
    """无缓存时从真实临时库读 active 行，variants 拆分；停用/confusable 不进入等价组。"""
    from app.database import get_db, init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "s.db"))
    invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','水灰比','W/C')")
    invalidate_lexicon_caches()
    eq = load_equivalent_groups()
    names = {g.canonical for g in eq}
    assert {"混凝土", "水灰比"} <= names
    row = next(g for g in eq if g.canonical == "水灰比")
    assert row.variants == ["W/C"]


def test_equiv_conflict_word_blocks_load(monkeypatch, tmp_path):
    """同一词分属两个 equiv 组 → load_equivalent_groups 返回空且记录冲突词"""
    from app.database import get_db, init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "c.db"))
    invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        # init_db 已预置 alias 混凝土/砼，先清空使下方两行成为唯一数据（避免唯一键重复）
        conn.execute("DELETE FROM lexicon_entries")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','混凝土','砼')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('synonym','钢筋','混凝土')")
    invalidate_lexicon_caches()
    assert load_equivalent_groups() == []
    assert store.equiv_conflict_word == "混凝土"
