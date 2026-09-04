# -*- coding: utf-8 -*-
"""#3b 待处理统计测试：get_pending_stats 口径（条文×维度）+ 展示片段 HTML"""
from app.classifier.batch_queue import add_to_queue, get_pending_stats
from app.database import init_db, get_db


def _seed(conn, n=5):
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES ('GB 50204', '测试规范', '现行')",
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    ids = []
    for i in range(n):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
            (sid, f"1.0.{i+1}", f"第{i+1}条 混凝土施工"),
        )
        ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return ids


def test_empty_queue_returns_zeros(monkeypatch, tmp_path):
    db_path = tmp_path / "ps.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    stats = get_pending_stats()
    assert stats["rows"] == 0
    assert stats["clauses"] == 0
    assert stats["by_dim"] == {}


def test_pending_stats_aggregates_rows_and_distinct_clauses(monkeypatch, tmp_path):
    """口径验证：pending 行数 > 涉及条文数（一条条文可多维待处理），二者都要统计"""
    db_path = tmp_path / "ps2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        ids = _seed(conn, n=5)
    # 前 3 条条文 dim4+dim6 双维入列 → 3*2=6 行；另 2 条仅 dim4 → 2 行；共 8 行 / 5 条文
    for i, cid in enumerate(ids):
        add_to_queue(cid, "dim4", 0.3)
        if i < 3:
            add_to_queue(cid, "dim6", 0.3)

    stats = get_pending_stats()
    assert stats["rows"] == 8
    assert stats["clauses"] == 5
    assert stats["by_dim"]["dim4"]["rows"] == 5
    assert stats["by_dim"]["dim4"]["clauses"] == 5
    assert stats["by_dim"]["dim6"]["rows"] == 3
    assert stats["by_dim"]["dim6"]["clauses"] == 3


def test_pending_stats_html_renders_counts(monkeypatch, tmp_path):
    """展示片段含条文数/项数与按维 tooltip；空队列显示'无待处理'"""
    from app.routes.rules_routes import _pending_stats_html

    html = _pending_stats_html({"rows": 8, "clauses": 5,
                                "by_dim": {"dim4": {"rows": 5, "clauses": 5},
                                           "dim6": {"rows": 3, "clauses": 3}}})
    assert "5 条文" in html and "/ 8 项" in html
    assert "dim4 5 项" in html and "dim6 3 项" in html
    assert "无待处理" not in html

    empty = _pending_stats_html({"rows": 0, "clauses": 0, "by_dim": {}})
    assert "无待处理" in empty
