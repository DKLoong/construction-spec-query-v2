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


def _seed_conflict_with_clean(monkeypatch, tmp_path, name="cc.db"):
    """冲突对（混凝土/砼 与 钢筋/混凝土）+ 一个干净组（水灰比）+ 一条 confusable"""
    from app.database import get_db, init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / name))
    invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM lexicon_entries")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','混凝土','砼')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('synonym','钢筋','混凝土')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','水灰比','W/C')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants,distinguish) "
                     "VALUES ('confusable','圈梁','构造柱','竖向构件不同')")
    invalidate_lexicon_caches()


def test_conflict_drops_only_conflicting_groups(monkeypatch, tmp_path):
    """同词跨组只剔除冲突组，其余组照常可用（不再整表作废）

    降级策略：把「发现冲突即丢弃整张表」改为「只剔除冲突组」——一次数据瑕疵
    不应让全部 509 条等价组 + 211 条 confusable 一起失效。
    """
    _seed_conflict_with_clean(monkeypatch, tmp_path)
    eq = load_equivalent_groups()
    names = {g.canonical for g in eq}
    assert names == {"水灰比"}, "只应剔除冲突组，干净组必须保留"
    assert store.equiv_conflict_word == "混凝土", "冲突词应记录（兼容字段）"


def test_confusable_survives_equiv_conflict(monkeypatch, tmp_path):
    """confusable 是独立命名空间，不受 equiv 冲突牵连"""
    _seed_conflict_with_clean(monkeypatch, tmp_path, name="cc2.db")
    pairs = load_confusable_pairs()
    assert len(pairs) == 1 and pairs[0].canonical == "圈梁"


def test_no_conflict_keeps_all(monkeypatch, tmp_path):
    """无冲突时全量保留（降级不得误伤正常数据）"""
    _seed_conflict_with_clean(monkeypatch, tmp_path, name="cc3.db")
    from app.database import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM lexicon_entries")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','混凝土','砼')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','水灰比','W/C')")
    invalidate_lexicon_caches()
    assert {g.canonical for g in load_equivalent_groups()} == {"混凝土", "水灰比"}
    assert store.equiv_conflict_word is None



def _seed_conflict_db(monkeypatch, tmp_path, name="wc.db"):
    """建一个含「已有组」的临时库：alias 混凝土={砼} + synonym 钢筋={钢筋}"""
    from app.database import get_db, init_db

    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / name))
    invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM lexicon_entries")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','混凝土','砼')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('synonym','钢结构','钢构')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants,distinguish) "
                     "VALUES ('confusable','圈梁','构造柱','竖向构件不同')")
    invalidate_lexicon_caches()


def test_find_equiv_conflict_detects_canonical_hit(monkeypatch, tmp_path):
    """新组的 canonical 已属别的组 → 报出该词"""
    from app.database import get_db
    from app.lexicon.store import find_equiv_conflict

    _seed_conflict_db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert find_equiv_conflict(conn, ["混凝土", "水泥"]) == "混凝土"


def test_find_equiv_conflict_detects_variant_hit(monkeypatch, tmp_path):
    """新组的 variant 落在别的组的 variants 里 → 也要报出（variants 同样占用词面）"""
    from app.database import get_db
    from app.lexicon.store import find_equiv_conflict

    _seed_conflict_db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert find_equiv_conflict(conn, ["水灰比", "砼"]) == "砼"


def test_find_equiv_conflict_is_cross_kind(monkeypatch, tmp_path):
    """跨 kind 也算冲突：alias 的词不能被 synonym 组再占用（读侧校验如此）"""
    from app.database import get_db
    from app.lexicon.store import find_equiv_conflict

    _seed_conflict_db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert find_equiv_conflict(conn, ["钢构"]) == "钢构"


def test_find_equiv_conflict_none_when_clean(monkeypatch, tmp_path):
    """全新词面无冲突"""
    from app.database import get_db
    from app.lexicon.store import find_equiv_conflict

    _seed_conflict_db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert find_equiv_conflict(conn, ["水灰比", "W/C"]) is None


def test_find_equiv_conflict_excludes_self(monkeypatch, tmp_path):
    """编辑自身时不得与自己冲突（exclude_id 排除本行）"""
    from app.database import get_db
    from app.lexicon.store import find_equiv_conflict

    _seed_conflict_db(monkeypatch, tmp_path)
    with get_db() as conn:
        row = conn.execute("SELECT id FROM lexicon_entries WHERE canonical='混凝土'").fetchone()
        assert find_equiv_conflict(conn, ["混凝土", "砼"], exclude_id=row["id"]) is None
        # 但引入别人的词仍要拦住
        assert find_equiv_conflict(conn, ["混凝土", "钢构"], exclude_id=row["id"]) == "钢构"


def test_find_equiv_conflict_ignores_confusable(monkeypatch, tmp_path):
    """confusable 独立命名空间，不参与 equiv 词面占用"""
    from app.database import get_db
    from app.lexicon.store import find_equiv_conflict

    _seed_conflict_db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert find_equiv_conflict(conn, ["圈梁", "构造柱"]) is None


def test_find_equiv_conflict_accepts_words_own_group_after_write(monkeypatch, tmp_path):
    """写入校验必须与读侧 _check_equiv_unique 同严格：通过校验的写入不得让读侧作废"""
    from app.database import get_db
    from app.lexicon.store import find_equiv_conflict

    _seed_conflict_db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert find_equiv_conflict(conn, ["水灰比", "W/C"]) is None
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) "
                     "VALUES ('alias','水灰比','W/C')")
    invalidate_lexicon_caches()
    # 读侧必须仍能正常加载（未被整表作废）
    assert len(load_equivalent_groups()) >= 3


def test_equiv_conflict_logged_at_error(monkeypatch, tmp_path, caplog):
    """冲突必须以 ERROR 级记录：这是「整表失效」而非普通告警

    WARNING 级在日志界面里不够醒目，历史上正因级别不够+不可见，静默了 13 天。
    """
    import logging

    from app.database import get_db, init_db

    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "c2.db"))
    invalidate_lexicon_caches()
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM lexicon_entries")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','混凝土','砼')")
        conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('synonym','钢筋','混凝土')")
    invalidate_lexicon_caches()

    with caplog.at_level(logging.ERROR, logger="app.lexicon.store"):
        assert load_equivalent_groups() == []

    assert any(r.levelno == logging.ERROR and "冲突" in r.getMessage()
               for r in caplog.records), "冲突应以 ERROR 级记录"

