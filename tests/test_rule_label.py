"""规则 label 语义测试：匹配词与赋值标签分离（修复强行打标）"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "rl.db"))
    init_db()


def test_bump_rule_new_stores_label(monkeypatch, tmp_path):
    """沉淀新规则时存确认标签 label"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        bump_rule(conn, "dim4", "钢筋", "specialty",
                  is_confirmed=True, new_rule_active=True, label="结构专业")
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE pattern='钢筋'"
        ).fetchone()
    assert rule["label"] == "结构专业"


def test_bump_rule_existing_backfills_label(monkeypatch, tmp_path):
    """历史 label 为空规则被带 label 沉淀时回填（纠正旧 pattern-当标签语义）"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
               priority, threshold, hit_count, confirmed, is_active, label)
               VALUES ('dim4','specialty','钢筋进场','keyword',0,0.6,3,3,1,NULL)"""
        )
        bump_rule(conn, "dim4", "钢筋进场", is_confirmed=True, label="结构专业")
        rule = conn.execute(
            "SELECT label FROM classification_rules WHERE pattern='钢筋进场'"
        ).fetchone()
    assert rule["label"] == "结构专业"


def test_bump_rule_locked_seed_not_backfilled(monkeypatch, tmp_path):
    """种子规则（locked=1）是人工权威词表，AI 沉淀路径不得回填改写其 label

    实证污染：dim6 种子规则「混凝土」（seed_rules.py 中 dim6 材料标签词）
    被 AI 沉淀路径回填成 label='钢筋'（源自 JGJ107 单文档的局部语料经验）。
    回填只应纠正「动态沉淀规则」，不应改写人工种子词表的语义。
    """
    _setup(monkeypatch, tmp_path)
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
               priority, threshold, hit_count, confirmed, is_active, label, locked)
               VALUES ('dim6','material','混凝土','keyword',1,0.5,0,0,1,NULL,1)"""
        )
        bump_rule(conn, "dim6", "混凝土", is_confirmed=True, label="钢筋")
        rule = conn.execute(
            "SELECT label, hit_count, confirmed FROM classification_rules WHERE pattern='混凝土'"
        ).fetchone()
    assert rule["label"] is None, "种子规则 label 不得被回填"
    # 命中与确认计数仍应正常递增（规则确实命中了，只是不改写其标签语义）
    assert rule["hit_count"] == 1
    assert rule["confirmed"] == 1


def test_classify_assigns_label_not_pattern():
    """规则匹配命中 → 赋值 label（而非匹配词 pattern）——修复强行打标"""
    from app.classifier.rule_engine import classify_clause
    rules = [{
        "id": 1, "dimension": "dim4", "pattern": "钢筋", "label": "结构专业",
        "match_type": "keyword", "priority": 1, "threshold": 0.4, "is_active": 1,
    }]
    scores, labels, ids = classify_clause("钢筋进场应检验屈服强度", ["第五章"], rules)
    assert labels.get("dim4") == "结构专业"
    assert ids.get("dim4") == 1


def test_classify_no_label_fallback_pattern():
    """旧规则 label 为 NULL → 兼容回退赋 pattern（保持旧行为不破坏既有规则）"""
    from app.classifier.rule_engine import classify_clause
    rules = [{
        "id": 2, "dimension": "dim4", "pattern": "钢筋", "label": None,
        "match_type": "keyword", "priority": 1, "threshold": 0.4, "is_active": 1,
    }]
    scores, labels, ids = classify_clause("钢筋进场", [], rules)
    assert labels.get("dim4") == "钢筋"
