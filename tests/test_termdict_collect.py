import pytest
from app.database import init_db, get_db
from app.classifier.batch_queue import collect_label_candidates
from app.termdict import invalidate_term_cache


def _db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(p))
    init_db()


def test_dict_labels_and_words_are_candidates(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code,title) VALUES ('GB1','规范')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id,clause_no,content,dim6_material)"
            " VALUES (?, '1', 'x', '历史旧值')", (sid,))
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,aliases,source)"
            " VALUES ('dim6','钢筋','钢筋','螺纹钢筋','manual')")
    invalidate_term_cache()
    cands = collect_label_candidates("dim6")
    # label 必在且居首；canonical/aliases 词面随后
    assert cands[0] == "钢筋"
    assert set(cands) >= {"钢筋", "螺纹钢筋"}
    assert "历史旧值" not in cands      # 词典非空 → 不回退现值


def test_empty_dict_falls_back_to_clause_values(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','规范')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id,clause_no,content,dim6_material)"
            " VALUES (?, '1', 'x', '钢筋,混凝土')", (sid,))
    invalidate_term_cache()
    assert set(collect_label_candidates("dim6")) == {"钢筋", "混凝土"}


def test_inactive_dict_word_not_in_candidates(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source,is_active)"
            " VALUES ('dim6','钢筋','钢筋','manual',1)")
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source,is_active)"
            " VALUES ('dim6','停用词','停用词','manual',0)")
    invalidate_term_cache()
    assert "停用词" not in collect_label_candidates("dim6")
