"""半监督闭环：auto_adopted 沉淀规则 + 低置信确认纯打标（词面沉淀仅 Tab2）"""
from app.database import init_db, get_db
from app.classifier import batch_queue as bq


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "rfl.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    from tests.conftest import setup_search_data
    with get_db() as conn:
        setup_search_data(conn)  # 3 条文
    return db_path


def _seed_spec_clause(conn):
    """写一条测试条文（含可提词内容），返回 clause_id"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB1', 'x')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO clauses (spec_id, clause_no, content) "
                 "VALUES (?, '1.1', '含 钢筋 与 试验 的条文内容')", (sid,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_auto_adopted_sinks_rule_active(monkeypatch, tmp_path):
    """auto_adopted 对已背书词命中递增（不新建规则、confirmed 不新增）"""
    _setup(monkeypatch, tmp_path)
    from app.classifier.batch_queue import add_to_queue, apply_ai_results
    from app.database import get_db as _g
    with _g() as conn:
        clause = conn.execute("SELECT id, content FROM clauses WHERE content LIKE '%钢筋%' LIMIT 1").fetchone()
        conn.execute("UPDATE clauses SET content='钢筋进场应检验屈服强度' WHERE id=?", (clause["id"],))
        # 预置已背书规则（confirmed≥1 且 is_active=1）→ '钢筋' 词 approved，auto 只做命中递增
        conn.execute(
            "INSERT INTO classification_rules (dimension, pattern, label, threshold, confirmed, is_active) "
            "VALUES ('dim4', '钢筋', '结构专业', 0.6, 1, 1)")
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
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE dimension='dim4' AND pattern='钢筋'"
        ).fetchone()
        q = conn.execute(
            "SELECT status FROM classification_queue WHERE clause_id=? AND dimension='dim4'",
            (clause["id"],),
        ).fetchone()
    assert q["status"] == "auto_adopted"
    assert rule is not None and rule["is_active"] == 1
    assert rule["confirmed"] == 1  # auto_adopted 未人工确认，confirmed 不累加
    assert rule["hit_count"] == 1  # 已背书规则命中递增（0→1）


def test_feedback_conf_no_longer_creates_rules(auth_client):
    """C12：人工确认不再按来源置信度生成/启用规则（高/低置信均不沉淀词面）。

    原「高置信→规则启用 / 低置信→规则待审」语义随词面沉淀出口迁至 Tab2 词面批准
    （`rule_pending.approve_rule`）而废除，本测试锁死 process_feedback 不再建规则。
    """
    from app.classifier.feedback import process_feedback
    confs = (0.95, 0.6)
    with get_db() as conn:
        cids = [_seed_spec_clause(conn) for _ in confs]
        for cid in cids:
            bq.try_enqueue(conn, cid, "dim4", 0.0)
            conn.execute("UPDATE classification_queue SET status='review' "
                         "WHERE clause_id=? AND dimension='dim4'", (cid,))
    for cid, conf in zip(cids, confs):
        process_feedback(cid, "dim4", "结构专业", source_conf=conf, patterns=["钢筋"])
    with get_db() as conn:
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        n_pending = conn.execute("SELECT COUNT(*) n FROM rule_pending").fetchone()["n"]
        done = conn.execute("SELECT COUNT(*) n FROM classification_queue WHERE status='done'").fetchone()["n"]
    assert done == len(confs)      # 纯打标：写列 + queue done 照常
    assert n_rules == 0            # 不再生成规则
    assert n_pending == 0          # 不再沉淀词面


def test_process_feedback_ignores_patterns_no_rule(auth_client):
    """低置信确认传 patterns 不再沉淀词面——纯打标（词面沉淀仅 Tab2）。"""
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='review', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
    process_feedback(cid, "dim6", "钢筋", source_conf=0.5, patterns=["钢筋"])
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
        n_rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
        st = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()["status"]
    assert c == "钢筋" and n_rules == 0 and st == "done"


def test_process_feedback_no_review_queue_noop(auth_client):
    """queue 非 review（已 done）时 process_feedback 不覆写列（C15）。"""
    from app.classifier.feedback import process_feedback
    with get_db() as conn:
        cid = _seed_spec_clause(conn)
        bq.try_enqueue(conn, cid, "dim6", 0.0)
        conn.execute("UPDATE classification_queue SET status='done', ai_label='钢筋' "
                     "WHERE clause_id=?", (cid,))
        conn.execute("UPDATE clauses SET dim6_material='混凝土' WHERE id=?", (cid,))
    process_feedback(cid, "dim6", "钢筋")
    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()["dim6_material"]
    assert c == "混凝土"   # 不覆写人工定案
