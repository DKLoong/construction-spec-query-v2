"""审核界面性能治理：索引存在性 + 分页契约。

背景：`classification_queue` 此前零索引，相关子查询退化为全表扫描，
Tab1 主查询实测 375 ms（生产库 4 138 pending / 4 625 queue 行）。
"""
from app.database import init_db, get_db


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    init_db()


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
