"""规则命中沉淀（rule_sink.bump_rule）测试"""
from app.database import init_db, get_db
from app.config import RULE_AUTO_ENABLE_RATIO, RULE_AUTO_ENABLE_MIN_HIT


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "rs.db"))
    init_db()


def test_bump_new_rule_inactive_by_default(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        bump_rule(conn, "dim4", "钢筋", "specialty", is_confirmed=True, new_rule_active=False)
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='钢筋'").fetchone()
    assert row is not None
    assert row["is_active"] == 0
    assert row["hit_count"] == 1 and row["confirmed"] == 1


def test_bump_new_rule_active(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        bump_rule(conn, "dim4", "混凝土", is_confirmed=False, new_rule_active=True)
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='混凝土'").fetchone()
    assert row["is_active"] == 1
    assert row["confirmed"] == 0  # 未人工确认不累加 confirmed


def test_bump_existing_rule_auto_enable(monkeypatch, tmp_path):
    """高正确率(confirmed/hit>=0.8 & hit>=5)自动启用"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
               priority, threshold, hit_count, confirmed, is_active)
               VALUES ('dim4','specialty','钢筋','keyword',0,0.6,4,4,0)"""
        )
        bump_rule(conn, "dim4", "钢筋", is_confirmed=True, new_rule_active=False)
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='钢筋'").fetchone()
    # hit 5, confirmed 5 → 5/5 >= 0.8 且 hit>=5 → 自动启用
    assert row["is_active"] == 1


def test_bump_existing_rule_auto_disable(monkeypatch, tmp_path):
    """低正确率(confirmed/hit<0.3 & hit>10)自动停用"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
               priority, threshold, hit_count, confirmed, is_active)
               VALUES ('dim4','specialty','废词','keyword',0,0.6,11,3,1)"""
        )
        bump_rule(conn, "dim4", "废词", is_confirmed=False, new_rule_active=False)  # 仅 hit++
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='废词'").fetchone()
    # hit 12, confirmed 3 → 3/12 < 0.3 且 hit>10 → 停用
    assert row["is_active"] == 0


def test_auto_adopted_rule_exempt_from_auto_disable(monkeypatch, tmp_path):
    """纯 AI 采纳规则（confirmed=0 无人工信号）豁免自动停用：连 hit 12 次仍保持启用"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
               priority, threshold, hit_count, confirmed, is_active)
               VALUES ('dim4','specialty','AI词','keyword',0,0.6,1,0,1)"""
        )
        # 连续命中 11 次（累计 hit 12），is_confirmed=False 仅 hit++，confirmed 恒 0
        for _ in range(11):
            bump_rule(conn, "dim4", "AI词", is_confirmed=False, new_rule_active=False)
        row = conn.execute("SELECT * FROM classification_rules WHERE pattern='AI词'").fetchone()
    assert row["hit_count"] == 12
    assert row["confirmed"] == 0
    assert row["is_active"] == 1  # confirmed=0 豁免自动停用，不被锁死
