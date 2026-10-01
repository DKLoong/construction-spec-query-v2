"""审核界面性能治理：索引存在性 + 分页契约。

背景：`classification_queue` 此前零索引，相关子查询退化为全表扫描，
Tab1 主查询实测 375 ms（生产库 4 138 pending / 4 625 queue 行）。
"""
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


def _seed_review_clause(conn, spec="GB1"):
    """seed 一条进 Tab1 主表的条文（pending 词 + queue review）。"""
    cid = _seed_clause(conn, spec)
    rp.insert_pending(conn, cid, "dim6", "钢筋", "钢筋", 0.9, "b1")
    bq.try_enqueue(conn, cid, "dim6", 0.0)
    conn.execute("UPDATE classification_queue SET status='review' WHERE clause_id=?", (cid,))
    return cid


def test_classification_queue_has_lookup_index(monkeypatch, tmp_path):
    """queue 表必须有 (clause_id, dimension, status) 索引，否则 EXISTS 子查询全表扫。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        names = [r["name"] for r in conn.execute("PRAGMA index_list(classification_queue)")]
    assert "idx_cq_clause_dim_status" in names


def test_rule_pending_has_clause_lookup_index(monkeypatch, tmp_path):
    """rule_pending 必须有 clause_id 打头的索引，否则 NOT EXISTS 子查询全表扫。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        names = [r["name"] for r in conn.execute("PRAGMA index_list(rule_pending)")]
    assert "idx_rp_clause_dim_status" in names


def test_clauses_has_spec_id_index(monkeypatch, tmp_path):
    """clauses 必须有 spec_id 索引：规范列表 LEFT JOIN 目前靠 AUTOMATIC COVERING INDEX
    临时兜底，该临时索引每次查询重建，成本随条文总量线性累加。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        names = [r["name"] for r in conn.execute("PRAGMA index_list(clauses)")]
    assert "idx_clauses_spec_id" in names


# ── 分页契约（limit=None = 全量，向后兼容）────────────────────────

def test_pending_clause_groups_limit_caps_groups(monkeypatch, tmp_path):
    """limit 截断组数，且被截断的组不触发候选词查询。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        cids = [_seed_review_clause(conn, f"GB{i}") for i in range(5)]
    all_groups = rp.pending_clause_groups(None)
    assert len(all_groups) == 5
    limited = rp.pending_clause_groups(None, limit=2)
    assert len(limited) == 2
    # 每个保留组仍带完整候选词（截断不得破坏组内数据）
    assert all(g["candidates"] for g in limited)
    assert {g["clause_id"] for g in limited} <= set(cids)


def test_pending_clause_groups_limit_none_returns_all(monkeypatch, tmp_path):
    """limit=None 保持全量语义（既有测试依赖）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        for i in range(3):
            _seed_review_clause(conn, f"GB{i}")
    assert len(rp.pending_clause_groups(None, limit=None)) == 3
    assert len(rp.pending_clause_groups(None)) == 3


def test_clause_group_total_counts_both_segments(monkeypatch, tmp_path):
    """total = 主表段 + 全驳段，且不受 limit 影响。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_review_clause(conn, "GB1")
        c2 = _seed_review_clause(conn, "GB2")
        # 把 c2 的 pending 词全驳 → 落进「全驳段」
        rp.set_status(conn, rp.clause_pending_ids(conn, c2, "dim6"), "rejected")
    assert rp.clause_group_total(None) == 2
    # 全驳段自身也受 limit 约束
    assert len(rp.rejected_clause_groups(None, limit=1)) == 1


def test_word_group_total_and_limit(monkeypatch, tmp_path):
    """Tab2：total 是词面组数；limit 截断组数。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        c2 = _seed_clause(conn, "GB2")
        rp.insert_pending(conn, c1, "dim6", "钢筋", "钢筋", 0.9, "b1")
        rp.insert_pending(conn, c2, "dim6", "混凝土", "混凝土", 0.8, "b2")
        rp.insert_pending(conn, c2, "dim6", "模板", "模板", 0.7, "b2")
    assert rp.word_group_total(None) == 3
    assert len(rp.pending_groups(None, limit=1)) == 1
    assert len(rp.pending_groups(None, limit=2)) == 2
    assert len(rp.pending_groups(None)) == 3


# ── 路由接 limit 并下发 total（HTTP 契约）────────────────────────

def test_clause_pending_respects_limit(auth_client):
    """端点接 limit：只约束渲染条数。

    ⚠ 此处**不**断言 total 文案——「已显示 X / 共 N 条」由 Task 5 的模板改动产生，
    本 Task 时尚不存在。total 的**数值**正确性由 Task 2 的
    `test_clause_group_total_counts_both_segments` 覆盖，**渲染**由 Task 5 覆盖。
    """
    with get_db() as conn:
        for i in range(5):
            _seed_review_clause(conn, f"GB{i}")
    resp = auth_client.get("/review/clause-pending?limit=2")
    assert resp.status_code == 200
    assert resp.text.count("clause-row-") == 2, "limit=2 应只渲染 2 个条文卡片"


def test_clause_pending_rejects_out_of_range_limit(auth_client):
    """limit 是外部输入：越界必须被挡下（项目规则 1.1）。"""
    assert auth_client.get("/review/clause-pending?limit=0").status_code == 422
    assert auth_client.get("/review/clause-pending?limit=501").status_code == 422
    assert auth_client.get("/review/clause-pending?limit=abc").status_code == 422


def test_clause_pending_default_limit_is_50(auth_client):
    """不传 limit 时默认 50（首屏不再全量渲染）。"""
    with get_db() as conn:
        for i in range(55):
            _seed_review_clause(conn, f"GB{i}")
    html = auth_client.get("/review/clause-pending").text
    assert html.count("clause-row-") == 50


def test_word_pending_respects_limit(auth_client):
    """Tab2 同样受 limit 约束（total 文案断言见 Task 5，理由同上）。"""
    with get_db() as conn:
        c1 = _seed_clause(conn, "GB1")
        for word in ["钢筋", "混凝土", "模板", "砂浆", "涂料"]:
            rp.insert_pending(conn, c1, "dim6", word, word, 0.9, "b1")
    resp = auth_client.get("/review/word-pending?limit=2")
    assert resp.status_code == 200
    assert resp.text.count('<tr id="word-group-') == 2
