"""存量碎片规则 → 规则级 pending 迁移脚本（scripts/migrate_pending_fragments.py）测试"""
from app.database import init_db, get_db
from scripts.migrate_pending_fragments import run_migration


def _seed_fragment_rule(conn, dimension, pattern, label, confirmed=0, is_active=1, locked=0):
    conn.execute(
        "INSERT INTO classification_rules (dimension, pattern, label, threshold, "
        "confirmed, is_active, locked) VALUES (?, ?, ?, 0.6, ?, ?, ?)",
        (dimension, pattern, label, confirmed, is_active, locked))


def test_run_migration_imports_fragments(monkeypatch, tmp_path):
    """碎片规则导入规则级 pending；locked 种子跳过；不改规则状态。"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "mig1.db"))
    init_db()
    with get_db() as conn:
        _seed_fragment_rule(conn, "dim6", "style", "样式")
        _seed_fragment_rule(conn, "dim5", "验收", "验收")
        _seed_fragment_rule(conn, "dim6", "钢筋", "钢筋", locked=1)  # 种子锁定跳过
        stats = run_migration(conn)
        rows = conn.execute(
            "SELECT dimension, pattern, label, clause_id FROM rule_pending "
            "WHERE clause_id IS NULL").fetchall()
    assert stats == {"imported": 2, "skipped_duplicate": 0, "skipped_seed_locked": 1}
    assert {(r["dimension"], r["pattern"], r["label"]) for r in rows} == \
        {("dim6", "style", "样式"), ("dim5", "验收", "验收")}
    assert all(r["clause_id"] is None for r in rows)
    # 不改任何 classification_rules 状态（仍启用）
    with get_db() as conn:
        act = conn.execute(
            "SELECT is_active FROM classification_rules WHERE pattern='style'").fetchone()["is_active"]
    assert act == 1


def test_run_migration_idempotent(monkeypatch, tmp_path):
    """二次运行幂等：同键已有规则级行 → 全部 duplicate，不产生第二行。"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "mig2.db"))
    init_db()
    with get_db() as conn:
        _seed_fragment_rule(conn, "dim6", "style", "样式")
        stats1 = run_migration(conn)
        stats2 = run_migration(conn)
        n = conn.execute(
            "SELECT COUNT(*) n FROM rule_pending WHERE clause_id IS NULL").fetchone()["n"]
    assert stats1 == {"imported": 1, "skipped_duplicate": 0, "skipped_seed_locked": 0}
    assert stats2 == {"imported": 0, "skipped_duplicate": 1, "skipped_seed_locked": 0}
    assert n == 1


def test_run_migration_skips_non_fragments(monkeypatch, tmp_path):
    """已确认/已停用/label 空/非 dim4-6/pattern 空 均不导入。"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "mig3.db"))
    init_db()
    with get_db() as conn:
        _seed_fragment_rule(conn, "dim6", "钢筋", "钢筋", confirmed=1, is_active=1)  # 已确认
        _seed_fragment_rule(conn, "dim6", "废词", "废词", confirmed=0, is_active=0)  # 已停用
        conn.execute(
            "INSERT INTO classification_rules (dimension, pattern, label, threshold, "
            "confirmed, is_active) VALUES ('dim6', '无标签', NULL, 0.6, 0, 1)")  # label 空
        _seed_fragment_rule(conn, "dim1", "GB", "GB")  # 非 dim4/5/6
        _seed_fragment_rule(conn, "dim6", "   ", "空格词")  # pattern strip 空
        stats = run_migration(conn)
        n = conn.execute("SELECT COUNT(*) n FROM rule_pending").fetchone()["n"]
    assert stats == {"imported": 0, "skipped_duplicate": 0, "skipped_seed_locked": 0}
    assert n == 0
