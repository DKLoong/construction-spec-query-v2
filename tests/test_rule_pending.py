import sqlite3

import pytest

from app.database import init_db, get_db
from app.classifier import rule_pending as rp
from app.classifier import batch_queue as bq


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


def test_key_state_rejected_over_approved_mixed(monkeypatch, tmp_path):
    """同键跨条文混态：一行 approved + 一行 rejected → rejected（驳回为最新人工决定）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c2, "dim6", "钢筋", "钢筋", 0.8, "b2")
        ids = rp.pending_ids(conn, "dim6", "钢筋", "钢筋")
        rp.set_status(conn, [ids[0]], "approved")
        rp.set_status(conn, [ids[1]], "rejected")
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "rejected"


def test_key_state_approved_only(monkeypatch, tmp_path):
    """同键仅 approved 行 → approved。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "钢筋", "钢筋"), "approved")
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "approved"


def test_key_state_rejected_only(monkeypatch, tmp_path):
    """同键仅 rejected 行 → rejected。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "钢筋", "钢筋"), "rejected")
        assert rp.key_state(conn, "dim6", "钢筋", "钢筋") == "rejected"


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
        # C17：真实主链 insert_pending 必伴随 queue review，补队列行方进 Tab1 主表
        bq.try_enqueue(conn, c1, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (c1,))
    groups = rp.pending_clause_groups("dim6")
    assert len(groups) == 1
    g = groups[0]
    assert g["spec_code"] == "GB1"
    assert g["clause_no"] == "1.1"
    assert "钢筋" in g["content"]
    assert {c["label"] for c in g["candidates"]} == {"钢筋", "混凝土"}
    assert rp.pending_clause_groups("dim5") == []


def test_pending_clause_groups_excludes_terminal_and_no_queue(auth_client):
    """queue 终态(auto_adopted) 或无 queue 行的 pending 条文都不进 Tab1 主表。"""
    with get_db() as conn:
        # 终态：auto_adopted 但有残留 pending 词
        c_term = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_term, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_term, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='auto_adopted', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_term,))
        # 无 queue：直插 pending
        c_noq = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_noq, "dim6", "钢筋", "钢筋", 0.9, "bT")
        # review：应保留
        c_rev = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_rev, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_rev, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (c_rev,))
    groups = rp.pending_clause_groups()
    cids = {g["clause_id"] for g in groups}
    assert c_rev in cids
    assert c_term not in cids and c_noq not in cids


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


def test_backfill_skips_terminal_and_writes_review(auth_client):
    """反联守卫：queue 已 done 来源条文不覆写（列保持原值）；queue 仍 review 的写列。"""
    with get_db() as conn:
        c_done = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_done, "dim6", "钢筋", "钢筋", 0.9, "bC")
        bq.try_enqueue(conn, c_done, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='done', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_done,))
        conn.execute("UPDATE clauses SET dim6_material='混凝土' WHERE id=?", (c_done,))
        c_rev = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_rev, "dim6", "钢筋", "钢筋", 0.9, "bC")
        bq.try_enqueue(conn, c_rev, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_rev,))
    n = 0
    with get_db() as conn:
        n = rp.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
    with get_db() as conn:
        vals = {r["id"]: r["dim6_material"] for r in conn.execute(
            "SELECT id, dim6_material FROM clauses").fetchall()}
        statuses = {r["clause_id"]: r["status"] for r in conn.execute(
            "SELECT clause_id, status FROM classification_queue").fetchall()}
    assert n == 1                          # 只写 c_rev
    assert vals[c_done] == "混凝土"         # 不被反联覆写
    assert vals[c_rev] == "钢筋"
    assert statuses[c_rev] == "done"


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


# ── apply_ai_results 裁决流（Task 2）────────────────────────────

def _seed_spec_clause(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB1', 'x')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id, clause_no, content) "
                 "VALUES (?, '1.1', '含 钢筋 与 试验 的条文内容')", (sid,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _run_batch(monkeypatch, tmp_path, conf=0.9):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bX', status='ai_processing' "
                     "WHERE clause_id=?", (cid,))
    bq.apply_ai_results("bX", [{"clause_id": cid, "label": "钢筋", "confidence": conf}])
    return cid


def test_first_time_word_goes_pending_not_auto(monkeypatch, tmp_path):
    """内容词首次（无规则 confirmed 无 pending approved）→ 该条 review 不写列，词入 pending。"""
    cid = _run_batch(monkeypatch, tmp_path)
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        p = conn.execute("SELECT COUNT(*) n FROM rule_pending WHERE status='pending'").fetchone()["n"]
    assert q["status"] == "review"
    assert (c["dim6_material"] or "") == ""
    assert p >= 1


def test_endorsed_word_autos_and_writes(monkeypatch, tmp_path):
    """同键规则 confirmed≥1 → auto_adopted 写列；首次词（试验）仍入池。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute("INSERT INTO classification_rules (dimension,pattern,label,threshold,confirmed,is_active)"
                     " VALUES ('dim6','钢筋','钢筋',0.6,1,1)")
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bY', status='ai_processing' WHERE clause_id=?", (cid,))
    bq.apply_ai_results("bY", [{"clause_id": cid, "label": "钢筋", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        p = conn.execute("SELECT pattern FROM rule_pending WHERE status='pending'").fetchall()
    assert q["status"] == "auto_adopted"
    assert c["dim6_material"] == "钢筋"
    assert any(r["pattern"] != "钢筋" for r in p)  # 试验 入池，钢筋 已背书不插


def test_rejected_word_skipped_no_write(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "z")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "钢筋", "钢筋"), "rejected")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bZ', status='ai_processing' WHERE clause_id=?", (cid,))
    bq.apply_ai_results("bZ", [{"clause_id": cid, "label": "钢筋", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
    assert q["status"] == "review"
    assert (c["dim6_material"] or "") == ""


def test_no_keywords_high_conf_autos_write_only(monkeypatch, tmp_path):
    """条文无提词（extract 空）但 conf 高 → 允许 auto 写列、不沉淀词（无词无夹带）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','x')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO clauses (spec_id,clause_no,content) VALUES (?, '1.1', '一')", (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bP', status='ai_processing' WHERE clause_id=?", (cid,))
    bq.apply_ai_results("bP", [{"clause_id": cid, "label": "钢筋", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        p = conn.execute("SELECT COUNT(*) n FROM rule_pending").fetchone()["n"]
    assert q["status"] == "auto_adopted"
    assert c["dim6_material"] == "钢筋"
    assert p == 0


# ── process_feedback 纯打标（Task 3 / C12）──────────────────────

def test_confirm_writes_col_ignores_patterns(monkeypatch, tmp_path):
    """低置信确认传 patterns 也不沉淀（C12：词面沉淀仅 Tab2）→ 写列 + queue done。"""
    from app.classifier.feedback import process_feedback
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)   # content '钢筋 条文'
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score, status) "
            "VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.95, patterns=["钢筋"])
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        n_rule = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        n_pending = conn.execute("SELECT COUNT(*) n FROM rule_pending").fetchone()["n"]
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"
    assert n_rule == 0                       # patterns 被忽略：不建规则
    assert n_pending == 0                    # 不沉淀词面


def test_confirm_no_patterns_writes_only(monkeypatch, tmp_path):
    """patterns=None → 纯打标：写列、不沉淀任何规则/词。"""
    from app.classifier.feedback import process_feedback
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score, status) "
            "VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.95)   # patterns=None → 纯打标
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        n_rule = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        n_pending = conn.execute("SELECT COUNT(*) n FROM rule_pending").fetchone()["n"]
    assert c["dim6_material"] == "钢筋"
    assert n_rule == 0
    assert n_pending == 0


def test_confirm_does_not_overwrite_rejected_word(monkeypatch, tmp_path):
    """低置信确认不覆写已驳回词面（C12）：rejected 行保持 rejected，不生成规则。"""
    from app.classifier.feedback import process_feedback
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.set_status(conn, rp.pending_ids(conn, "dim6", "钢筋", "钢筋"), "rejected")
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score, status) "
            "VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.95, patterns=["钢筋"])
    with get_db() as conn:
        st = conn.execute(
            "SELECT status FROM rule_pending WHERE clause_id=? AND dimension='dim6' "
            "AND pattern='钢筋' AND label='钢筋'", (cid,)).fetchone()
        n_rule = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
    assert st["status"] == "rejected"   # 驳回保持：词面复核只在 Tab2
    assert n_rule == 0
    assert c["dim6_material"] == "钢筋"  # 打标照写


# ── 词面校核 / 黑名单管理端点（Task 4）───────────────────────────

def test_decide_approve_key_level_backfills_and_bumps(auth_client):
    """词面 approve 键级：同键多条文都 approved + 回填写列 + queue done + confirmed 规则生成。"""
    with get_db() as conn:
        cids = [_seed_spec_clause(conn), _seed_spec_clause(conn)]
        for cid in cids:
            rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bA")
            bq.try_enqueue(conn, cid, "dim6", 0.0)
            conn.execute(
                "UPDATE classification_queue SET batch_id='bA', status='review', "
                "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        # 同组未勾选键「混凝土」应被反义驳回
        rp.insert_pending(conn, cids[0], "dim6", "钢筋", "混凝土", 0.7, "bA")
        gid = conn.execute(
            "SELECT id FROM rule_pending WHERE clause_id=? AND label='钢筋'",
            (cids[0],)).fetchone()["id"]
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = {r["label"]: r["status"] for r in conn.execute(
            "SELECT label, status FROM rule_pending WHERE pattern='钢筋'").fetchall()}
        cols = sorted(r["dim6_material"] or "" for r in conn.execute(
            "SELECT dim6_material FROM clauses").fetchall())
        qs = sorted(r["status"] for r in conn.execute(
            "SELECT status FROM classification_queue").fetchall())
        rules = conn.execute(
            "SELECT confirmed, is_active FROM classification_rules "
            "WHERE dimension='dim6' AND pattern='钢筋' AND label='钢筋'").fetchall()
    assert st == {"钢筋": "approved", "混凝土": "rejected"}
    assert cols == ["钢筋", "钢筋"]        # 两条条文都回填
    assert qs == ["done", "done"]          # queue 都 done
    assert len(rules) == 1 and rules[0]["confirmed"] >= 1 and rules[0]["is_active"] == 1


def test_decide_reject_no_write_queue_stays(auth_client):
    """词面 reject：被 reject 键条文不写列、queue 滞留 review（GC10）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bA")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET batch_id='bA', status='review', "
            "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        gid = conn.execute("SELECT id FROM rule_pending WHERE label='钢筋'").fetchone()["id"]
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "reject"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (gid,)).fetchone()["status"]
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert st == "rejected"
    assert (c["dim6_material"] or "") == ""   # 不写列
    assert q["status"] == "review"            # queue 滞留


def test_decide_reject_unchecked_keys_backfilled_f2(auth_client):
    """reject 时同组未勾选键转 approved 并回填+bump（F2：最终 approved 键都回填）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bA")
        rp.insert_pending(conn, cid, "dim6", "钢筋", "混凝土", 0.7, "bA")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET batch_id='bA', status='review', "
            "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        gid = conn.execute("SELECT id FROM rule_pending WHERE label='钢筋'").fetchone()["id"]
    # 勾选「钢筋」reject → 钢筋 rejected；未勾「混凝土」转 approved 并回填
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "reject"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = {r["label"]: r["status"] for r in conn.execute(
            "SELECT label, status FROM rule_pending WHERE pattern='钢筋'").fetchall()}
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        rule = conn.execute(
            "SELECT confirmed, is_active, label FROM classification_rules "
            "WHERE dimension='dim6' AND pattern='钢筋'").fetchone()
    assert st == {"钢筋": "rejected", "混凝土": "approved"}
    assert c["dim6_material"] == "混凝土"   # 未勾键回填
    assert q["status"] == "done"
    assert rule is not None and rule["confirmed"] >= 1 and rule["label"] == "混凝土"


def test_blacklist_restore_then_approve(auth_client):
    """黑名单两档：restore 置 pending；再 reject 后 approve 写列+queue done+confirmed 规则。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "试验", "钢筋", 0.9, "bB")
        pid = rp.pending_ids(conn, "dim6", "试验", "钢筋")[0]
        rp.set_status(conn, [pid], "rejected")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='review', batch_id='bB' WHERE clause_id=?", (cid,))
    # 档1：恢复待审
    resp = auth_client.post(f"/review/blacklist/{pid}/restore")
    assert resp.status_code == 200
    with get_db() as conn:
        assert conn.execute(
            "SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"] == "pending"
    # 重新驳回，再档2：批准
    with get_db() as conn:
        rp.set_status(conn, [pid], "rejected")
    resp = auth_client.post(f"/review/blacklist/{pid}/approve")
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        rule = conn.execute(
            "SELECT confirmed, is_active, label FROM classification_rules "
            "WHERE dimension='dim6' AND pattern='试验'").fetchone()
    assert st == "approved"
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"
    assert rule is not None and rule["confirmed"] >= 1 and rule["label"] == "钢筋"


# ── Tab1 确认 = 只写列（打标-沉淀解耦 C1/C3/C4/C13/C14）────────────

def test_tab1_approve_writes_col_keeps_checked_rejects_unchecked(auth_client):
    """确认=只写列+queue done；勾选词留 pending；去勾词 rejected；不建规则。dimension 必填。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.7, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', batch_id='bT', "
                     "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        reject = [r["id"] for r in conn.execute(
            "SELECT id FROM rule_pending WHERE clause_id=? AND pattern='混凝土'", (cid,)).fetchall()]
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": reject, "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = {r["pattern"]: r["status"] for r in conn.execute(
            "SELECT pattern, status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "钢筋" and q["status"] == "done"
    assert st["钢筋"] == "pending" and st["混凝土"] == "rejected"
    assert n_rules == 0


def test_tab1_approve_empty_ids_pure_confirm(auth_client):
    """ids 空=纯确认：只写列+done，无词驳回、无规则。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', batch_id='bT', "
                     "ai_label='钢筋' WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
        st = conn.execute("SELECT status FROM rule_pending WHERE clause_id=?", (cid,)).fetchone()["status"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c == "钢筋" and q == "done" and st == "pending" and n_rules == 0


def test_tab1_decide_reject_action_now_400(auth_client):
    """reject action 已删 → 400。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [pid], "action": "reject"})
    assert resp.status_code == 400


def test_tab1_approve_no_review_queue_400(auth_client):
    """无 review queue 项 → 400，不写列不改列。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [], "action": "approve"})
    assert resp.status_code == 400
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
    assert (c or "") == ""


def test_tab1_decide_out_of_scope_ids_400(auth_client):
    """decide 作用域守卫（项目规则 1.1）：ids 带别的条文的 pending id → 400，
    且那条条文的词仍 pending、其碎片规则仍启用、本条文不写列（守卫须在任何写入之前）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        other = _seed_spec_clause(conn)
        _seed_rule(conn, "dim6", "试验", "试验", confirmed=0, is_active=1)
        rp.insert_pending(conn, other, "dim6", "试验", "试验", 0.9, "bO")
        other_pid = rp.clause_pending_ids(conn, other, "dim6")[0]
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [other_pid], "action": "approve"})
    assert resp.status_code == 400
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?",
                          (other_pid,)).fetchone()["status"]
        rule = conn.execute("SELECT is_active FROM classification_rules "
                            "WHERE dimension='dim6' AND pattern='试验'").fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?",
                         (cid,)).fetchone()["dim6_material"]
    assert st == "pending"          # 越界词未被驳回
    assert rule["is_active"] == 1   # 越界词碎片未被停用
    assert (c or "") == ""          # 越界请求整体 no-op，本条文不写列


def test_tab1_approve_non_string_dimension_400(auth_client):
    """dimension 传非字符串（如数组）→ 400 而非 500（外部输入类型校验）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": ["dim6"], "ids": [], "action": "approve"})
    assert resp.status_code == 400


def test_tab1_approve_missing_dimension_400(auth_client):
    """dimension 缺失 → 400（外部输入必填校验）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"ids": [], "action": "approve"})
    assert resp.status_code == 400


def test_tab1_approve_non_int_ids_400(auth_client):
    """ids 含非整数元素（dict/list 不可哈希）→ 400 而非 500（外部输入类型校验）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [{"x": 1}], "action": "approve"})
    assert resp.status_code == 400
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert (c or "") == "" and q == "review"   # 未写列、队列未推进


def test_confirm_clause_rejects_invalid_dimension(monkeypatch, tmp_path):
    """_confirm_clause 内置维度白名单：非法维度直接 ValueError，不拼进列名（防御注入面）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
        with pytest.raises(ValueError):
            rp._confirm_clause(conn, cid, "dim6; DROP TABLE clauses", "钢筋")
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert q["status"] == "review"   # 抛错前未动队列/未写列


def test_tab1_approve_empty_ai_label_400(auth_client):
    """review 存在但 ai_label 为空 → 400（避免静默 no-op 页面卡死）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label=NULL "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": [], "action": "approve"})
    assert resp.status_code == 400


# ── Tab1 条文多标签 + 行内编辑（Task 5）────────────────────────────

def test_tab1_clause_multi_label_approve_partial(auth_client):
    """同条文两候选只勾 A（保留）→ 写列取 queue ai_label；A 保持 pending，去勾的 B rejected。

    新语义（C1/C4）：确认只写该条分类列 + queue done，词面沉淀交 Tab2——勾选词不动、
    去勾词进黑名单、不建规则。
    """
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        for lab in ("钢筋", "混凝土"):
            rp.insert_pending(conn, cid, "dim6", "钢筋", lab, 0.9, "bT")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', batch_id='bT', "
                     "ai_label='钢筋' WHERE clause_id=?", (cid,))
        unchecked = [r["id"] for r in conn.execute(
            "SELECT id FROM rule_pending WHERE clause_id=? AND dimension='dim6' "
            "AND label='混凝土' AND status='pending'", (cid,)).fetchall()]
    resp = auth_client.post(f"/review/clause-pending/{cid}/decide",
                            json={"dimension": "dim6", "ids": unchecked, "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        st = {r["label"]: r["status"] for r in conn.execute(
            "SELECT label, status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "钢筋"
    assert st["钢筋"] == "pending" and st["混凝土"] == "rejected"
    assert q["status"] == "done"
    assert n_rules == 0


def test_tab1_inline_remove_label_rejects(auth_client):
    """inline 删除预填标签 → 该 (pattern,label) 组合 reject。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [pid],
                                  "new_label": None, "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
    assert st == "rejected"


def test_tab1_inline_new_label_writes_col_only(auth_client):
    """驳回后 inline 编辑输入新标签 → 只写列（纯打标）；残留 pending 词被驳（C9），不沉淀规则（F7）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
        rp.set_status(conn, [pid], "rejected")            # F7 场景：先删光预填标签
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.8, "bU")  # 残留 pending 词
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        stale = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        res = conn.execute("SELECT status FROM rule_pending WHERE clause_id=? AND pattern='混凝土' "
                           "AND status='pending'", (cid,)).fetchone()
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "混凝土" and q["status"] == "done"
    assert stale == "rejected" and res is None   # 残留 pending 词已被驳，无 pending 残留
    assert n_rules == 0


def test_tab1_inline_edit_rejects_invalid_dimension(auth_client):
    """inline-edit 外部输入非法 dimension → 400（白名单校验，防 SQL 注入/非法列）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土",
                                  "dimension": "dim6; DROP TABLE clauses"})
    assert resp.status_code == 400


def test_tab1_inline_cancel_noop(auth_client):
    """取消（仅读请求/不提交）不改变任何 pending/queue/列状态。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
    resp = auth_client.get("/review/clause-pending")
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert st == "pending"
    assert q["status"] == "review"
    assert (c["dim6_material"] or "") == ""
    assert n_rules == 0


def test_tab1_inline_keep_words_stay_pending_no_rule(auth_client):
    """inline 保留词不再 approve：保持 pending、不写列、不 bump（词面沉淀仅 Tab2）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        pid = rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [pid], "removed_label_ids": [],
                                  "new_label": None, "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert st == "pending" and n_rules == 0


def test_tab1_inline_new_label_rejects_residual_words(auth_client):
    """new_label≠ai_label → 残留 pending 词全 rejected（防 Tab2 反嚼覆写）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.8, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = {r["pattern"]: r["status"] for r in conn.execute(
            "SELECT pattern, status FROM rule_pending WHERE clause_id=?", (cid,)).fetchall()}
    assert c["dim6_material"] == "混凝土" and q["status"] == "done"
    assert st["钢筋"] == "rejected" and st["混凝土"] == "rejected"


def test_tab1_inline_new_label_no_review_queue_noop(auth_client):
    """new_label 但无 review queue → no-op：不抛错、不改列、不驳词。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        st = conn.execute("SELECT status FROM rule_pending WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert (c or "") == "" and st == "pending"


def test_tab1_inline_cross_dim_ambiguous_400(auth_client):
    """dimension 缺失且该 clause 跨多 review 维 → 400（C13 不再从任意 id 反查）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        for dim in ("dim6", "dim5"):
            rp.insert_pending(conn, cid, dim, "钢筋", "钢筋", 0.9, "bU")
            bq.try_enqueue(conn, cid, dim, 0.0)
            conn.execute("UPDATE classification_queue SET status='review' "
                         "WHERE clause_id=? AND dimension=?", (cid, dim))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土"})  # 无 dimension
    assert resp.status_code == 400


def test_tab1_inline_edit_non_string_dimension_400(auth_client):
    """inline-edit dimension 传非字符串（如数组）→ 400 而非 500（外部输入类型校验）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": ["dim6"]})
    assert resp.status_code == 400
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert (c or "") == "" and q == "review"   # 未写列、队列未推进


def test_tab1_inline_edit_non_int_ids_400(auth_client):
    """label_ids/removed_label_ids 含非整数元素（dict 不可哈希）→ 400 而非 500。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [{"x": 1}], "removed_label_ids": [],
                                  "new_label": None, "dimension": "dim6"})
    assert resp.status_code == 400
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert st == "pending"   # 词面状态未被触及


def test_tab1_inline_new_label_rejects_residual_when_ai_label_null(auth_client):
    """ai_label 为 NULL 时 new_label 仍触发 C9 驳残留词（NULL 视为与任何非空新标签不同）。

    该状态可达：queue 行 INSERT 无 ai_label，decide 遇空标签也提示用户改用编辑输入。
    """
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.8, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label=NULL "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "混凝土", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = conn.execute("SELECT status FROM rule_pending WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert c["dim6_material"] == "混凝土" and q["status"] == "done"
    assert st == "rejected"   # C9 未被 ai_label=NULL 静默跳过


def test_tab1_inline_new_label_same_as_ai_label_keeps_residual_pending(auth_client):
    """new_label 与原 ai_label 相同 → 不驳残留词（人类原样保留给 Tab2 的词不得被误驳）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "混凝土", "钢筋", 0.8, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": "钢筋", "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        st = conn.execute("SELECT status FROM rule_pending WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert c["dim6_material"] == "钢筋" and q["status"] == "done"
    assert st == "pending"   # val == orig → 残留词保留给 Tab2


def test_tab1_inline_edit_non_string_new_label_400(auth_client):
    """new_label 传非字符串（如数组）→ 400，不把字面量 "['a']" 写进分类列。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [],
                                  "new_label": ["a"], "dimension": "dim6"})
    assert resp.status_code == 400
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert (c or "") == "" and q == "review"   # 未写列、队列未推进


def test_clause_pending_get_includes_queue_fallback(auth_client):
    """GET /review/clause-pending 返回含低置信 queue 兜底条文（无 pending 词）。"""
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB1', 'x')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO clauses (spec_id, clause_no, content) "
                     "VALUES (?, '1.1', '低置信兜底条文内容')", (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO classification_queue (clause_id, dimension, keyword_score, status) "
                     "VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    resp = auth_client.get("/review/clause-pending")
    assert resp.status_code == 200
    assert "低置信兜底条文内容" in resp.text


# ── Task 6：一致性/幂等 edge 补测 + 低置信兜底回归 ────────────────────

def test_word_approve_then_clause_queue_already_done_idempotent(auth_client):
    """词面批准后 Tab1 该条文已 done；重复 approve 无副作用（confirmed 不重复递增）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bC")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bC', status='review', "
                     "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        gid = conn.execute("SELECT id FROM rule_pending WHERE label='钢筋'").fetchone()["id"]
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c1 = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q1 = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
        r1 = conn.execute("SELECT confirmed FROM classification_rules "
                          "WHERE dimension='dim6' AND pattern='钢筋'").fetchone()["confirmed"]
    # 重复 approve 同一 gid（已 approved）→ 无 pending 键 → 不写列/不 bump
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c2 = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q2 = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
        r2 = conn.execute("SELECT confirmed FROM classification_rules "
                          "WHERE dimension='dim6' AND pattern='钢筋'").fetchone()["confirmed"]
        pending_n = conn.execute("SELECT COUNT(*) n FROM rule_pending WHERE status='pending'").fetchone()["n"]
    assert (q1, c1, r1) == (q2, c2, r2) == ("done", "钢筋", 1)
    assert pending_n == 0


def test_lowconf_clause_without_pending_still_in_tab1(auth_client):
    """低置信、无 pending 词的 queue review 条文仍在 Tab1（queue 兜底），confirm 纯打标写列、无规则沉淀。"""
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB1', 'x')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO clauses (spec_id, clause_no, content) "
                     "VALUES (?, '1.1', '低置信兜底条文内容')", (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO classification_queue (clause_id, dimension, keyword_score, "
                     "status, ai_label, ai_confidence) "
                     "VALUES (?, 'dim6', 0.2, 'review', '钢筋', 0.5)", (cid,))
        qid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    resp = auth_client.get("/review/clause-pending")
    assert resp.status_code == 200
    assert "低置信兜底条文内容" in resp.text
    # confirm 走纯打标（patterns 空）→ 写列 + 无规则沉淀
    resp = auth_client.post(f"/review/{qid}/confirm", json={"patterns": []})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert c["dim6_material"] == "钢筋"
    assert n_rules == 0


def test_clause_decide_idempotent(monkeypatch, tmp_path):
    """同 clause 同作用域重复 approve：第二次无新副作用（回填/queue done/pending 幂等）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        a = rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, cid, "dim6", "钢筋", "混凝土", 0.7, "b1")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', batch_id='b1' WHERE clause_id=?", (cid,))
        # 端点等价顺序：先回填（仍 pending）再置 approved
        n1 = rp.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
        assert n1 == 1
        r = rp.decide_scope(conn, [a], "approve", expand=True)
        assert r == {"approved": 1, "rejected": 1}
    with get_db() as conn:
        # 重复 approve：回填无 pending 来源 → 0；decide 无 pending 键 → 空
        n2 = rp.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
        r2 = rp.decide_scope(conn, [a], "approve", expand=True)
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        pending_n = conn.execute("SELECT COUNT(*) n FROM rule_pending WHERE status='pending'").fetchone()["n"]
    assert n2 == 0
    assert r2 == {"approved": 0, "rejected": 0}
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"
    assert pending_n == 0


def test_tab2_approve_then_tab1_gone(auth_client):
    """Tab2 词面批准某组合后，Tab1 对应条文（该组合来源）消失（queue done + 回填）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bA")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET batch_id='bA', status='review', "
                     "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        gid = conn.execute("SELECT id FROM rule_pending WHERE label='钢筋'").fetchone()["id"]
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert c == "钢筋"
    assert q == "done"
    resp = auth_client.get("/review/clause-pending")
    assert resp.status_code == 200
    assert "含 钢筋 与 试验 的条文内容" not in resp.text


def test_reject_keeps_clause_in_tab1_marked(auth_client):
    """D1：候选全 reject 后条文仍在 Tab1 主表，带「词已驳回」标记（非兜底块）+ 行内编辑可用。

    全驳唯一可达路径是行内编辑删光预填标签（Tab1 批量驳回已移除，approve 会写列 done 使行消失）。
    """
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        for lab in ("钢筋", "混凝土"):
            rp.insert_pending(conn, cid, "dim6", "钢筋", lab, 0.9, "bA")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', batch_id='bA' WHERE clause_id=?", (cid,))
        all_ids = rp.clause_pending_ids(conn, cid, "dim6")
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": all_ids,
                                  "new_label": None, "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert q["status"] == "review"   # 行内全驳不写列不改队列 → 条文仍以「词已驳回」留在主表
    resp = auth_client.get("/review/clause-pending")
    assert resp.status_code == 200
    assert "词已驳回" in resp.text
    assert "含 钢筋 与 试验 的条文内容" in resp.text   # 仍在 Tab1 主表
    assert "输入新标签" in resp.text                    # 行内编辑可用


# ── 规则级 pending（clause_id 可空）─────────────────────────────

def test_init_db_rule_pending_clause_nullable(monkeypatch, tmp_path):
    """新库 rule_pending.clause_id 可空（notnull=0），且规则级 partial 唯一索引存在。"""
    from app.database import init_db, get_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()
    with get_db() as conn:
        cols = conn.execute("PRAGMA table_info(rule_pending)").fetchall()
        clause = next(c for c in cols if c["name"] == "clause_id")
        assert clause["notnull"] == 0
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_rule_pending_rule_key'").fetchone()
        assert idx is not None


def test_init_db_migrates_rule_pending_clause_nullable(monkeypatch, tmp_path):
    """旧库 clause_id NOT NULL → init_db 重建为可空，且数据保留 + 新 partial 索引建好。"""
    from app.database import init_db, get_db
    db_path = tmp_path / "old.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE rule_pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT NOT NULL,
            pattern TEXT NOT NULL, label TEXT NOT NULL, clause_id INTEGER NOT NULL,
            ai_confidence REAL, batch_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE INDEX idx_rule_pending_key ON rule_pending(dimension, pattern, label, status);
        CREATE INDEX idx_rule_pending_status ON rule_pending(status);
        CREATE UNIQUE INDEX idx_rule_pending_uniq ON rule_pending(dimension, pattern, label, clause_id);
        INSERT INTO rule_pending (dimension, pattern, label, clause_id, status)
            VALUES ('dim6', '钢筋', '钢筋', 7, 'pending');
    """)
    conn.close()
    init_db()  # 触发 NOT NULL → 可空重建
    with get_db() as conn:
        cols = conn.execute("PRAGMA table_info(rule_pending)").fetchall()
        clause = next(c for c in cols if c["name"] == "clause_id")
        assert clause["notnull"] == 0
        row = conn.execute(
            "SELECT clause_id, dimension, pattern, label FROM rule_pending").fetchone()
        assert row["clause_id"] == 7
        assert row["pattern"] == "钢筋"
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_rule_pending_rule_key'").fetchone()
        assert idx is not None


def test_insert_pending_rule_level_idempotent(monkeypatch, tmp_path):
    """规则级（clause_id=None）同键二次 None；与条文级行共存。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        a = rp.insert_pending(conn, None, "dim6", "检验", "钢筋", 0.9, "b1")
        b = rp.insert_pending(conn, None, "dim6", "检验", "钢筋", 0.8, "b1")
        assert a is not None
        assert b is None
        cid = _seed_clause(conn, "GB1")
        c = rp.insert_pending(conn, cid, "dim6", "检验", "钢筋", 0.7, "b2")
        assert c is not None and c != a
        row = conn.execute(
            "SELECT clause_id FROM rule_pending WHERE id=?", (a,)).fetchone()
        assert row["clause_id"] is None


def test_backfill_skips_rule_level(monkeypatch, tmp_path):
    """规则级 pending（clause_id NULL）不写列、不清 queue。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cid = _seed_clause(conn)
        rp.insert_pending(conn, None, "dim6", "钢筋", "钢筋", 0.9, "b1")
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, status) "
            "VALUES (?, 'dim6', 'review')", (cid,))
        n = rp.backfill_and_close(conn, "dim6", "钢筋", "钢筋")
        assert n == 0  # 规则级无来源条文
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert (c["dim6_material"] or "") == ""   # 不写列
    assert q["status"] == "review"            # 不清 queue


def test_decide_reject_deactivates_fragment(auth_client):
    """词面 reject → 关联 confirmed=0 碎片规则 is_active=0（同 pattern 同 label）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        _seed_rule(conn, "dim6", "钢筋", "钢筋", confirmed=0, is_active=1)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bA")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET batch_id='bA', status='review', "
            "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        gid = conn.execute("SELECT id FROM rule_pending WHERE label='钢筋'").fetchone()["id"]
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "reject"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (gid,)).fetchone()["status"]
        rule = conn.execute(
            "SELECT confirmed, is_active FROM classification_rules "
            "WHERE dimension='dim6' AND pattern='钢筋' AND label='钢筋'").fetchone()
    assert st == "rejected"
    assert rule["confirmed"] == 0
    assert rule["is_active"] == 0


def test_decide_approve_revives_fragment(auth_client):
    """词面 approve → 存量停用碎片规则复活 is_active=1 且 confirmed≥1。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        _seed_rule(conn, "dim6", "钢筋", "钢筋", confirmed=0, is_active=0)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bA")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET batch_id='bA', status='review', "
            "ai_label='钢筋', ai_confidence=0.9 WHERE clause_id=?", (cid,))
        gid = conn.execute("SELECT id FROM rule_pending WHERE label='钢筋'").fetchone()["id"]
    resp = auth_client.post("/review/word-pending/decide",
                            json={"ids": [gid], "action": "approve"})
    assert resp.status_code == 200
    with get_db() as conn:
        rule = conn.execute(
            "SELECT confirmed, is_active FROM classification_rules "
            "WHERE dimension='dim6' AND pattern='钢筋' AND label='钢筋'").fetchone()
    assert rule["confirmed"] >= 1
    assert rule["is_active"] == 1


def test_inline_removed_deactivates_fragment(auth_client):
    """inline 删除预填标签 → 关联 confirmed=0 碎片规则 is_active=0。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        _seed_rule(conn, "dim6", "钢筋", "钢筋", confirmed=0, is_active=1)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        pid = rp.clause_pending_ids(conn, cid, "dim6")[0]
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [pid],
                                  "new_label": None, "dimension": "dim6"})
    assert resp.status_code == 200
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?", (pid,)).fetchone()["status"]
        rule = conn.execute(
            "SELECT confirmed, is_active FROM classification_rules "
            "WHERE dimension='dim6' AND pattern='钢筋' AND label='钢筋'").fetchone()
    assert st == "rejected"
    assert rule["confirmed"] == 0
    assert rule["is_active"] == 0


def test_tab1_inline_out_of_scope_removed_ids_400(auth_client):
    """inline 作用域守卫（项目规则 1.1）：removed_label_ids 带别的条文的 pending id →
    400，且那条条文的词仍 pending、其碎片规则仍启用、本条文不写列。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        other = _seed_spec_clause(conn)
        _seed_rule(conn, "dim6", "试验", "试验", confirmed=0, is_active=1)
        rp.insert_pending(conn, other, "dim6", "试验", "试验", 0.9, "bO")
        other_pid = rp.clause_pending_ids(conn, other, "dim6")[0]
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bU")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    resp = auth_client.post(f"/review/clause-pending/{cid}/inline-edit",
                            json={"label_ids": [], "removed_label_ids": [other_pid],
                                  "new_label": None, "dimension": "dim6"})
    assert resp.status_code == 400
    with get_db() as conn:
        st = conn.execute("SELECT status FROM rule_pending WHERE id=?",
                          (other_pid,)).fetchone()["status"]
        rule = conn.execute("SELECT is_active FROM classification_rules "
                            "WHERE dimension='dim6' AND pattern='试验'").fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?",
                         (cid,)).fetchone()["dim6_material"]
    assert st == "pending"          # 越界词未被驳回
    assert rule["is_active"] == 1   # 越界词碎片未被停用
    assert (c or "") == ""          # 越界请求整体 no-op，本条文不写列


def test_word_pending_get_renders_rule_level(auth_client):
    """词面校核 GET 能渲染规则级 pending（无来源条文，clause_count 0）。"""
    with get_db() as conn:
        rp.insert_pending(conn, None, "dim6", "style", "样式", 0.8, "frag")
    resp = auth_client.get("/review/word-pending")
    assert resp.status_code == 200
    assert "style" in resp.text   # 规则级词面出现在聚合列表
    assert "样式" in resp.text


# ── 审核红点计数（Tab1 条文组 + Tab2 词面组）─────────────────────

def test_pending_counts_zero_when_empty(auth_client):
    """无待审时红点计数为 0。"""
    resp = auth_client.get("/review/pending-count")
    assert resp.status_code == 200
    assert resp.json() == {"clause": 0, "word": 0}


def test_pending_counts_clause_and_word_groups(auth_client):
    """pending 行按 (clause,dim) 聚合计条文组、按 (dim,pattern) 聚合计词面组。"""
    with get_db() as conn:
        c1 = _seed_spec_clause(conn)
        c2 = _seed_spec_clause(conn)
        # 同一 (clause1, dim6) 两个词 → 条文组只 +1，词面组 +2
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b")
        rp.insert_pending(conn, c1, "dim6", "试验", "钢筋", 0.8, "b")
        # 跨条文同词同 dim → 条文组 +1（clause2），词面组不新增
        rp.insert_pending(conn, c2, "dim6", "钢筋", "钢筋", 0.9, "b")
        # 新维度新词面 → 两方向都 +1
        rp.insert_pending(conn, c2, "dim5", "梁", "梁", 0.7, "b")
        # C17：clause 与 Tab1 主表同口径，需存在 queue review 行
        for cid, dim in ((c1, "dim6"), (c2, "dim6"), (c2, "dim5")):
            bq.try_enqueue(conn, cid, dim, 0.0)
            conn.execute(
                "UPDATE classification_queue SET status='review' WHERE clause_id=? AND dimension=?",
                (cid, dim))
    resp = auth_client.get("/review/pending-count")
    j = resp.json()
    assert j["clause"] == 3      # (c1,dim6) (c2,dim6) (c2,dim5)
    assert j["word"] == 3        # (dim6,钢筋) (dim6,试验) (dim5,梁)


def test_pending_counts_rejected_needs_relabel(auth_client):
    """候选全 reject 且 queue 仍 review 的条文计入 clause（Tab1 重标待办）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        pid = rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b")
        rp.set_status(conn, [pid], "rejected")
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
    resp = auth_client.get("/review/pending-count")
    j = resp.json()
    assert j["clause"] == 1
    assert j["word"] == 0        # 无 pending 词面


def test_pending_counts_lowconf_fallback(auth_client):
    """低置信 queue review 且无 pending/rejected 关联 → 计入 clause（Tab1 兜底）。"""
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute(
            "UPDATE classification_queue SET status='review', ai_label='钢筋', "
            "ai_confidence=0.5 WHERE clause_id=?", (cid,))
    resp = auth_client.get("/review/pending-count")
    j = resp.json()
    assert j["clause"] == 1
    assert j["word"] == 0


def test_pending_counts_aligned_with_tab1_source(auth_client):
    """pending_counts.clause = 主表组 + 词全驳组 + 低置信兜底（已确认 done 的残留 pending 词不计入）。"""
    with get_db() as conn:
        c_rev = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_rev, "dim6", "钢筋", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_rev, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_rev,))
        # 已确认 done 的条文，其勾选词仍 pending → 不计 clause 待审
        c_done = _seed_spec_clause(conn)
        rp.insert_pending(conn, c_done, "dim6", "混凝土", "钢筋", 0.9, "bT")
        bq.try_enqueue(conn, c_done, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='done', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_done,))
        # 低置信兜底：review 无词
        c_low = _seed_spec_clause(conn)
        bq.try_enqueue(conn, c_low, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (c_low,))
    counts = rp.pending_counts()
    assert counts["clause"] == 2   # c_rev + 低置信 c_low；c_done 不计
    assert counts["word"] == 2     # 钢筋 + 混凝土 两个词面组


def test_pending_counts_count_sql_matches_group_functions(auth_client):
    """COUNT 版与 group 函数逐字同口径：孤儿行（clause 已删）不计、同 clause 多维度分组去重。"""
    with get_db() as conn:
        # 正常 review 组
        cid = _seed_spec_clause(conn)
        rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "bT")
        rp.insert_pending(conn, cid, "dim6", "试验", "钢筋", 0.8, "bT")
        rp.insert_pending(conn, cid, "dim5", "梁", "梁", 0.7, "bT")
        for dim in ("dim6", "dim5"):
            bq.try_enqueue(conn, cid, dim, 0.0)
            conn.execute(
                "UPDATE classification_queue SET status='review' WHERE clause_id=? AND dimension=?",
                (cid, dim))
        # 孤儿 pending/rejected 行：clause_id 指向不存在的条文 → JOIN 应排除
        rp.insert_pending(conn, 9999, "dim6", "孤儿", "钢筋", 0.9, "bT")
        rp.insert_pending(conn, 9998, "dim6", "孤儿驳", "钢筋", 0.9, "bT")
        conn.execute("UPDATE rule_pending SET status='rejected' WHERE clause_id=9998")
    groups = len(rp.pending_clause_groups()) + len(rp.rejected_clause_groups())
    counts = rp.pending_counts()
    # 本场景兜底段为 0 → clause 应等于两个 group 函数之和，且孤儿行不参与分组
    assert groups == 2           # (cid,dim6) (cid,dim5)，无孤儿组
    assert counts["clause"] == groups
    assert counts["word"] == 4   # (dim6,钢筋)(dim6,试验)(dim5,梁)(dim6,孤儿) 词面组
