import pytest
from app.database import init_db, get_db


def _db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(p))
    init_db()


def _insert(conn, dimension, label, canonical, aliases=""):
    conn.execute(
        "INSERT INTO term_labels (dimension, label, canonical, aliases, source, note)"
        " VALUES (?, ?, ?, ?, 'manual', '')",
        (dimension, label, canonical, aliases))


def test_is_valid_label_member_and_nonmember(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _insert(conn, "dim6", "钢筋", "钢筋")
    from app.termdict import is_valid_label
    assert is_valid_label("dim6", "钢筋") is True
    assert is_valid_label("dim6", "style") is False


def test_is_valid_label_non_collected_dim_always_true(monkeypatch, tmp_path):
    """dim2/3 不在词典域，恒放行（不影响既有流程）。"""
    _db(monkeypatch, tmp_path)
    from app.termdict import is_valid_label
    assert is_valid_label("dim2", "结构") is True
    assert is_valid_label("dim1", "国家标准") is True


def test_empty_dict_passes_through(monkeypatch, tmp_path):
    """词典尚无任何词条 → 放行（收口启用前的过渡）。"""
    _db(monkeypatch, tmp_path)
    from app.termdict import is_valid_label, valid_labels
    assert is_valid_label("dim6", "钢筋") is True
    assert valid_labels("dim6") == []


def test_valid_labels_and_load(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _insert(conn, "dim6", "钢筋", "钢筋")
        _insert(conn, "dim6", "混凝土", "混凝土", "砼")
        _insert(conn, "dim4", "结构", "结构")
    from app.termdict import valid_labels, load_active_entries
    assert sorted(valid_labels("dim6")) == ["混凝土", "钢筋"]
    rows = load_active_entries("dim6")
    assert {r.label for r in rows} == {"混凝土", "钢筋"}
    assert [r.aliases for r in rows if r.label == "混凝土"] == [["砼"]]
    assert len(load_active_entries()) == 3


def test_word_conflict_invalidates_and_passes(monkeypatch, tmp_path):
    """同维词面「砼」指向两个 label → 词典禁载(放行) + 冲突词暴露。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _insert(conn, "dim6", "混凝土", "混凝土", "砼")
        _insert(conn, "dim6", "水泥", "水泥", "砼")   # 砼 同维归属两 label
    import app.termdict.store as st
    from app.termdict import is_valid_label, valid_labels
    assert is_valid_label("dim6", "钢筋") is True   # 词典不可信 → 放行
    assert valid_labels("dim6") == []
    assert st.word_conflict == ("dim6", "砼")       # 冲突词由加载期一致性校验暴露


def test_invalidate_reloads(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    from app.termdict import is_valid_label, invalidate_term_cache
    with get_db() as conn:
        _insert(conn, "dim6", "钢筋", "钢筋")
    assert is_valid_label("dim6", "钢筋") is True
    with get_db() as conn:
        conn.execute("DELETE FROM term_labels")
    assert is_valid_label("dim6", "钢筋") is True   # 旧缓存未失效仍命中
    invalidate_term_cache()
    assert is_valid_label("dim6", "钢筋") is True   # 重载后词典空 → 空词典放行


def test_load_failure_passes_through(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    import app.termdict.store as st
    def boom():
        raise RuntimeError("db broken")
    monkeypatch.setattr(st, "_fresh", boom)
    from app.termdict import is_valid_label
    assert is_valid_label("dim6", "钢筋") is True
