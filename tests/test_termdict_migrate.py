"""存量迁移脚本测试（scripts/migrate_term_dict.py）。

注（偏离 task-8-brief 原文的数据修正，语义保持一致）：
brief 原文把 confirmed=0 碎片 style/检验 的 label 设为 '钢筋'，但声明语义
（Spec §7.2 / 脚本 SQL）停用条件是「confirmed=0 且 label ∉ 新词典」——而
(dim6,'钢筋') 恰是本用例入典的权威 label，style/检验 带着它 **不会** 被停用，
disabled 应为 0 而非 2，与用例自身断言/注释矛盾。
修正：让碎片携带**不在新词典**的 label（保温/防水），使其真正落入
「label ∉ 新词典 → 停用」判定，用例结构与断言不变（inserted=1 / disabled=2 /
canonical=钢筋 / 验收 label 空不动 / 幂等重跑）。
"""
import pytest
from app.database import init_db, get_db


def _db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(p))
    init_db()


def _seed_rules(conn):
    # locked seed：干净 label，应入典
    conn.execute(
        "INSERT INTO classification_rules (dimension,pattern,label,threshold,hit_count,confirmed,locked,is_active)"
        " VALUES ('dim6','钢筋','钢筋',0.6,150,131,1,1)")
    # confirmed>0：干净 label，应入典（canonical 取 hit 最高 pattern）
    conn.execute(
        "INSERT INTO classification_rules (dimension,pattern,label,threshold,hit_count,confirmed,is_active)"
        " VALUES ('dim6','接头','钢筋',0.6,20,3,1)")
    conn.execute(
        "INSERT INTO classification_rules (dimension,pattern,label,threshold,hit_count,confirmed,is_active)"
        " VALUES ('dim6','套筒','钢筋',0.6,30,1,1)")
    # confirmed=0 碎片：label ∉ 新词典（保温/防水未入典），应停用
    conn.execute(
        "INSERT INTO classification_rules (dimension,pattern,label,threshold,hit_count,confirmed,is_active)"
        " VALUES ('dim6','style','保温',0.6,17,0,1)")
    conn.execute(
        "INSERT INTO classification_rules (dimension,pattern,label,threshold,hit_count,confirmed,is_active)"
        " VALUES ('dim6','检验','防水',0.6,15,0,1)")
    # label 为空：不动
    conn.execute(
        "INSERT INTO classification_rules (dimension,pattern,threshold,hit_count,confirmed,is_active)"
        " VALUES ('dim6','验收',0.6,9,1,1)")


def test_migration_builds_dict_and_disables_fragments(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_rules(conn)
    from scripts.migrate_term_dict import run_migration
    stats = run_migration()
    assert stats["inserted"] == 1          # 钢筋/钢筋 → 仅一个 (dim6,钢筋)
    assert stats["disabled"] == 2          # style/检验 碎片停用
    with get_db() as conn:
        t = conn.execute("SELECT canonical, source FROM term_labels WHERE label='钢筋'").fetchone()
        r = conn.execute("SELECT is_active FROM classification_rules WHERE pattern='style'").fetchone()
        r2 = conn.execute("SELECT is_active FROM classification_rules WHERE pattern='接头'").fetchone()
        r3 = conn.execute("SELECT is_active FROM classification_rules WHERE pattern='验收'").fetchone()
    assert t["canonical"] == "钢筋"         # hit 最高 150 > 套筒30
    assert r["is_active"] == 0
    assert r2["is_active"] == 1
    assert r3["is_active"] == 1            # label 空不动


def test_migration_is_idempotent(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_rules(conn)
    from scripts.migrate_term_dict import run_migration
    run_migration()
    with get_db() as conn:
        n1 = conn.execute("SELECT COUNT(*) n FROM term_labels").fetchone()["n"]
    run_migration()
    with get_db() as conn:
        n2 = conn.execute("SELECT COUNT(*) n FROM term_labels").fetchone()["n"]
    assert n1 == n2 == 1
