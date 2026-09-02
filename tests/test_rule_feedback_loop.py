"""半监督闭环：auto_adopted 沉淀规则 + 人工确认高置信自动启用"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "rfl.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    from tests.conftest import setup_search_data
    with get_db() as conn:
        setup_search_data(conn)  # 3 条文
    return db_path


def test_auto_adopted_sinks_rule_active(monkeypatch, tmp_path):
    """auto_adopted 分支沉淀新规则且 is_active=1，confirmed 不累加"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.batch_queue import add_to_queue, apply_ai_results
    from app.database import get_db as _g
    with _g() as conn:
        clause = conn.execute("SELECT id, content FROM clauses WHERE content LIKE '%钢筋%' LIMIT 1").fetchone()
        conn.execute("UPDATE clauses SET content='钢筋进场应检验屈服强度' WHERE id=?", (clause["id"],))
    # 构造 pending 批次：add_to_queue 后手动置 batch_id + ai_processing（模拟 get_pending_batch）。
    # 注意 add_to_queue 内部会另开 get_db() 写连接，必须等上方 UPDATE 所在事务提交后再调用，
    # 否则两个写连接在 WAL 下互相锁死（database is locked）。
    add_to_queue(clause["id"], "dim4", 0.0)
    with _g() as conn:
        conn.execute(
            "UPDATE classification_queue SET batch_id='b1', status='ai_processing' "
            "WHERE clause_id=? AND dimension='dim4'", (clause["id"],),
        )
    apply_ai_results("b1", [
        {"clause_id": clause["id"], "label": "结构专业", "confidence": 0.85},
    ])
    with _g() as conn:
        # 关键词由 extract_keywords 提取，内容「钢筋进场应检验屈服强度」首词为「钢筋进场」
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE dimension='dim4' AND pattern='钢筋进场'"
        ).fetchone()
        q = conn.execute(
            "SELECT status FROM classification_queue WHERE clause_id=? AND dimension='dim4'",
            (clause["id"],),
        ).fetchone()
    assert q["status"] == "auto_adopted"
    assert rule is not None and rule["is_active"] == 1
    assert rule["confirmed"] == 0  # auto_adopted 未人工确认，confirmed 不累加
    assert rule["hit_count"] == 1  # 新规则创建即记首次命中（bump_rule 语义）


def test_feedback_high_conf_rule_auto_active(monkeypatch, tmp_path):
    """人工确认高置信(>=0.9)生成新规则 is_active=1"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        clause = conn.execute("SELECT id, content FROM clauses WHERE content LIKE '%钢筋%' LIMIT 1").fetchone()
        # 确保该条关键词语料能提取到独立关键词
        conn.execute("UPDATE clauses SET content='钢筋进场应检验屈服强度' WHERE id=?", (clause["id"],))
    process_feedback(clause["id"], "dim4", "结构专业", source_conf=0.95)
    with get_db() as conn:
        rule = conn.execute("SELECT * FROM classification_rules WHERE dimension='dim4' AND pattern='钢筋进场'").fetchone()
        # 关键词含 钢筋进场 → 新规则；高置信 → is_active=1；人工确认来源首条即记 confirmed=1
        assert rule is not None
        assert rule["is_active"] == 1
        assert rule["confirmed"] == 1
        assert rule["hit_count"] == 1  # 新规则创建即记首次命中


def test_feedback_low_conf_rule_inactive(monkeypatch, tmp_path):
    """人工确认低置信(<0.9)生成新规则 is_active=0 待审核"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        clause = conn.execute("SELECT id, content FROM clauses WHERE content LIKE '%钢筋%' LIMIT 1").fetchone()
        conn.execute("UPDATE clauses SET content='钢筋进场应检验屈服强度' WHERE id=?", (clause["id"],))
    process_feedback(clause["id"], "dim4", "结构专业", source_conf=0.6)
    with get_db() as conn:
        rule = conn.execute("SELECT * FROM classification_rules WHERE dimension='dim4' AND pattern='钢筋进场'").fetchone()
        # 低置信 → 新规则初态 inactive，待审核
        assert rule is not None
        assert rule["is_active"] == 0
