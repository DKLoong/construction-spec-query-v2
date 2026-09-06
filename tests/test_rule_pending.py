import sqlite3

import pytest

from app.database import init_db, get_db
from app.classifier import rule_pending as rp


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()


def _seed_clause(conn, spec="GB1"):
    conn.execute("INSERT INTO specifications (code, title) VALUES (?, 'x')", (spec,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, '1.1', '钢筋 条文')", (sid,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_rule(conn, dimension, pattern, label, confirmed=1, is_active=1):
    conn.execute(
        "INSERT INTO classification_rules (dimension, pattern, label, threshold, confirmed, is_active) "
        "VALUES (?, ?, ?, 0.6, ?, ?)",
        (dimension, pattern, label, confirmed, is_active))


# ── key_state 三态 ─────────────────────────────────────────────

def test_key_state_none_then_approved_by_rule_confirmed(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "none"
        _seed_rule(conn, "dim6", "钢筋", "钢筋")
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "approved"


def test_key_state_inactive_rule_not_approved(monkeypatch, tmp_path):
    """被停用的历史确认规则不算背书（F3/F8：需 is_active=1）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_rule(conn, "dim6", "钢筋", "钢筋", confirmed=1, is_active=0)
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "none"


def test_key_state_null_label_rule_approved(monkeypatch, tmp_path):
    """规则 label 为 NULL（旧规则/规则页手工建）也算背书（F3/F8）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_rule(conn, "dim6", "钢筋", None)
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "approved"


def test_key_state_pending_approved(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "钢筋", "钢筋"), "approved")
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "approved"


def test_key_state_rejected_priority_over_rule(monkeypatch, tmp_path):
    """pending 侧 rejected（黑名单）覆盖规则侧 confirmed 背书。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "试验", "钢筋", 0.9, "b1")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "试验", "钢筋"), "rejected")
        _seed_rule(conn, "dim6", "试验", "钢筋")
        assert rp.key_state(conn, "dim6", "试验", "钢筋") == "rejected"


# ── insert_pending 幂等 + UNIQUE ───────────────────────────────

def test_insert_pending_idempotent(monkeypatch, tmp_path):
    """同 (clause,key) 二次 None；不同 clause 同键各自成行（F5 修正）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        a = rp.insert_pending(conn, c1, "dim6", "检验", "钢筋", 0.9, "b1")
        b = rp.insert_pending(conn, c1, "dim6", "检验", "钢筋", 0.8, "b1")
        c = rp.insert_pending(conn, c2, "dim6", "检验", "钢筋", 0.7, "b2")
        assert a is not None
        assert b is None
        assert c is not None and c != a


def test_insert_pending_unique_backstop(monkeypatch, tmp_path):
    """UNIQUE(dimension,pattern,label,clause_id) 兜底：裸重复 INSERT 抛 IntegrityError。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "检验", "钢筋", 0.9, "b1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rule_pending (dimension, pattern, label, clause_id) "
                "VALUES (?, ?, ?, ?)",
                ("dim6", "检验", "钢筋", cid))


# ── decide_scope 键级裁决 ──────────────────────────────────────

def test_decide_scope_approve_rejects_unchecked(monkeypatch, tmp_path):
    """approve 且勾选 1/3 → 勾选 approved、其余 2 rejected（键级反义）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        ids = []
        for lab in ("钢筋", "混凝土", "水泥"):
            ids.append(rp.insert_pending(conn, cid, "dim6", "检验", lab, 0.9, "b1"))
        r = rp.decide_scope(conn, [ids[0]], "approve", expand=False)
        assert r == {"approved": 1, "rejected": 2}
        assert rp.key_state(conn, "dim6", "检验", "钢筋") == "approved"
        assert rp.key_state(conn, "dim6", "检验", "混凝土") == "rejected"
        assert rp.key_state(conn, "dim6", "检验", "水泥") == "rejected"


def test_decide_scope_key_level_across_clauses(monkeypatch, tmp_path):
    """同键多条文跨条文都 approved；同组未勾键 rejected（F1/F2 键级裁决）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        id_a = rp.insert_pending(conn, c1, "dim6", "检验", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c2, "dim6", "检验", "钢筋", 0.9, "b2")
        rp.insert_pending(conn, c1, "dim6", "检验", "混凝土", 0.7, "b1")
        r = rp.decide_scope(conn, [id_a], "approve", expand=True)
        assert r == {"approved": 2, "rejected": 1}
        assert rp.key_state(conn, "dim6", "检验", "钢筋") == "approved"
        assert rp.key_state(conn, "dim6", "检验", "混凝土") == "rejected"


def test_decide_scope_reject_reverse(monkeypatch, tmp_path):
    """reject：勾选键 rejected、同组未勾键 approved。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        ids = []
        for lab in ("钢筋", "混凝土"):
            ids.append(rp.insert_pending(conn, cid, "dim6", "检验", lab, 0.9, "b1"))
        r = rp.decide_scope(conn, [ids[0]], "reject", expand=True)
        assert r == {"approved": 1, "rejected": 1}
        assert rp.key_state(conn, "dim6", "检验", "钢筋") == "rejected"
        assert rp.key_state(conn, "dim6", "检验", "混凝土") == "approved"


# ── 聚合 / 黑名单 / 条文分组 ────────────────────────────────────

def test_pending_groups_and_blacklist(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c2, "dim6", "钢筋", "钢筋", 0.7, "b2")
        rp.insert_pending(conn, c1, "dim6", "试验", "检验", 0.6, "b1")
    groups = rp.pending_groups("dim6")
    assert {g["pattern"] for g in groups} == {"钢筋", "试验"}
    gw = next(g for g in groups if g["pattern"] == "钢筋")
    assert gw["clause_count"] == 2


def test_pending_clause_groups_joins_text(monkeypatch, tmp_path):
    """按 clause 聚合 + join 带 spec_code/clause_no/content，且维度过滤正确。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "混凝土", 0.7, "b1")
    groups = rp.pending_clause_groups("dim6")
    assert len(groups) == 1
    g = groups[0]
    assert g["spec_code"] == "GB1"
    assert g["clause_no"] == "1.1"
    assert "钢筋" in g["content"]
    assert {c["label"] for c in g["candidates"]} == {"钢筋", "混凝土"}
    assert rp.pending_clause_groups("dim5") == []


def test_clause_pending_ids(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        a = rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        b = rp.insert_pending(conn, c1, "dim6", "钢筋", "混凝土", 0.7, "b1")
        rp.insert_pending(conn, c2, "dim6", "钢筋", "钢筋", 0.6, "b2")
        ids = rp.clause_pending_ids(conn, c1, "dim6")
        assert sorted(ids) == sorted([a, b])


def test_blacklist_rows(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c2, "dim6", "钢筋", "钢筋", 0.7, "b2")
        pids = rp.pending_ids(conn, "dim6", "钢筋", "钢筋")
        rp.set_status(conn, pids, "rejected")
    rows = rp.blacklist_rows()
    assert len(rows) == 1
    assert rows[0]["pattern"] == "钢筋"
    assert rows[0]["label"] == "钢筋"
    assert rows[0]["n"] == 2
    assert rows[0]["clause_count"] == 2


# ── backfill 写列 + 清 queue ───────────────────────────────────

def test_backfill_and_close_writes_col_and_done_queue(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, status) "
            "VALUES (?, 'dim6', 'review')", (cid,))
        n = rp.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
        assert n == 1
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"


def test_key_all_pending_ids(monkeypatch, tmp_path):
    """按行 id 反查键，取该键指定状态的全部行（黑名单 restore/approve 用）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        a = rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        b = rp.insert_pending(conn, c2, "dim6", "钢筋", "钢筋", 0.7, "b2")
        rp.set_status(conn, [a, b], "rejected")
        ids = rp._key_all_pending_ids(conn, a, "rejected")
        assert sorted(ids) == sorted([a, b])
        assert rp._key_all_pending_ids(conn, a, "pending") == []
