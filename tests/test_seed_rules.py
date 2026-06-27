"""种子规则脚本测试"""
import pytest
from unittest.mock import patch
import sys
from pathlib import Path

# 确保 scripts 目录可导入
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def test_seed_rules_inserts_rules(monkeypatch, tmp_path):
    """验证 seed_rules 正确插入分类规则"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()

    # 动态导入 seed_rules 模块
    sys.path.insert(0, str(SCRIPTS_DIR))
    import seed_rules
    seed_rules.seed(conn=None)

    with get_db() as conn:
        rules = conn.execute(
            "SELECT dimension, COUNT(*) as cnt FROM classification_rules "
            "WHERE is_active = 1 GROUP BY dimension ORDER BY dimension"
        ).fetchall()

    rule_counts = {r["dimension"]: r["cnt"] for r in rules}
    # 六个维度都应被覆盖
    for dim in ["dim1", "dim2", "dim3", "dim4", "dim5", "dim6"]:
        assert dim in rule_counts, f"{dim} 缺少规则"
        assert rule_counts[dim] >= 1, f"{dim} 规则数 {rule_counts[dim]} < 1"

    print(f"规则分布: {rule_counts}")
    total = sum(rule_counts.values())
    assert total >= 30, f"总规则数 {total} < 30，覆盖不够"


def test_seed_rules_is_idempotent(monkeypatch, tmp_path):
    """重复执行 seed 不应产生重复规则"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    sys.path.insert(0, str(SCRIPTS_DIR))
    import seed_rules

    seed_rules.seed(conn=None)
    with get_db() as conn:
        first_count = conn.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0]

    seed_rules.seed(conn=None)
    with get_db() as conn:
        second_count = conn.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0]

    assert first_count == second_count, (
        f"幂等失败：第一次 {first_count} 条，第二次 {second_count} 条"
    )


def test_seed_rules_structure(monkeypatch, tmp_path):
    """验证种子规则的数据结构完整性"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    sys.path.insert(0, str(SCRIPTS_DIR))
    import seed_rules
    seed_rules.seed(conn=None)

    with get_db() as conn:
        rules = conn.execute(
            "SELECT * FROM classification_rules WHERE is_active = 1"
        ).fetchall()

    for r in rules:
        assert r["dimension"] in [f"dim{i}" for i in range(1, 7)], (
            f"无效维度: {r['dimension']}"
        )
        assert r["pattern"] and r["pattern"].strip(), (
            f"空关键词: id={r['id']}, dim={r['dimension']}"
        )
        assert r["match_type"] in ("keyword", "regex", "exact"), (
            f"无效 match_type: {r['match_type']}"
        )
        assert 0 <= r["priority"] <= 10, (
            f"优先级越界: {r['priority']}"
        )
        assert 0 < r["threshold"] <= 1.0, (
            f"阈值越界: {r['threshold']}"
        )


def test_seed_rules_keyword_works(monkeypatch, tmp_path):
    """验证种子规则中的关键词能实际匹配条文"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    sys.path.insert(0, str(SCRIPTS_DIR))
    import seed_rules
    seed_rules.seed(conn=None)

    from app.classifier.rule_engine import classify_clause

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM classification_rules WHERE is_active = 1"
        ).fetchall()
    rules = [dict(r) for r in rows]

    # 测试常见条文关键词
    results = [
        ("混凝土强度应符合设计要求", "dim6"),  # 材料 → 混凝土
        ("钢筋绑扎应牢固", "dim6"),            # 材料 → 钢筋
        ("屋面防水层应连续铺设", "dim5"),       # 部位 → 屋面
        ("模板支撑应可靠", "dim5"),            # 部位 → 模板工程
        ("给水管道应做水压试验", "dim4"),       # 专业 → 给排水
        ("电气设备应接地", "dim4"),            # 专业 → 电气
    ]

    for text, expected_dim in results:
        scores, labels = classify_clause(text, [], rules)
        max_dim = max(scores, key=scores.get)
        assert scores[expected_dim] > 0, (
            f"'{text}' 未能匹配 {expected_dim}，scores={ {k: round(v,2) for k,v in scores.items()} }"
        )
