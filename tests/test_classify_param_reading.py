"""classify 参数读取改造测试：DB 覆盖生效（batch_size/置信阈值/规则启停/质量报表）"""
from app.database import init_db, get_db
from app.params import registry


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "cls.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()
    registry.clear_param_cache()


def _set(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     (key, str(value)))
    registry.clear_param_cache()


def _seed_clause_queue(conn, n, dim="dim4"):
    """插入 n 条 pending 队列（各对应一条条文），返回 clause_id 列表"""
    conn.execute("INSERT INTO specifications (code, title, status) VALUES ('GB/T 50000-2010', '测试', '现行')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    cids = []
    for i in range(n):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) "
            "VALUES (?, ?, '条', ?, '')",
            (sid, f"{i}.1", f"内容{i}"))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, 0.0)",
            (cid, dim))
        cids.append(cid)
    return cids


def test_batch_size_override_affects_pending_batch(monkeypatch, tmp_path):
    from app.classifier.batch_queue import get_pending_batch
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_clause_queue(conn, 3)
    _set("classify.batch_size", 2)
    rows = get_pending_batch("dim4", force=False)
    assert len(rows) == 2  # 默认 20 时 3 条不满批应返回 []; 覆盖 2 后取回 2


def test_ai_confidence_threshold_override(monkeypatch, tmp_path):
    from app.classifier.batch_queue import apply_ai_results
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        _seed_clause_queue(conn, 2)
        conn.execute("UPDATE classification_queue SET batch_id = 'BX', status = 'ai_processing'")
    _set("classify.ai_confidence_threshold", 0.9)
    apply_ai_results("BX", [
        {"clause_id": 1, "confidence": 0.95, "label": "钢筋"},
        {"clause_id": 2, "confidence": 0.85, "label": "混凝土"},
    ])
    with get_db() as conn:
        statuses = dict(conn.execute(
            "SELECT clause_id, status FROM classification_queue WHERE batch_id='BX'").fetchall())
    assert statuses[1] == "auto_adopted"  # 0.95 ≥ 0.9
    assert statuses[2] == "review"        # 0.85 < 0.9


def test_bump_rule_auto_enable_uses_db_min_hit(monkeypatch, tmp_path):
    from app.classifier.rule_sink import bump_rule
    _setup(monkeypatch, tmp_path)
    # 阶段一：min_hit=7，命中到 6 仍不足 → 不自动启用（每阶段独立提交，_set 不在写事务内嵌套）
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold, hit_count, confirmed, is_active) "
            "VALUES ('dim4', 't', '钢筋', 'keyword', 0, 0.6, 5, 5, 0)")
    _set("classify.rule_auto_enable_min_hit", 7)
    with get_db() as conn:
        bump_rule(conn, "dim4", "钢筋", is_confirmed=True)
        st1 = conn.execute(
            "SELECT is_active FROM classification_rules WHERE pattern='钢筋'").fetchone()["is_active"]
    # 阶段二：min_hit=4，命中到 7 达标 → 自动启用
    _set("classify.rule_auto_enable_min_hit", 4)
    with get_db() as conn:
        bump_rule(conn, "dim4", "钢筋", is_confirmed=True)
        st2 = conn.execute(
            "SELECT is_active FROM classification_rules WHERE pattern='钢筋'").fetchone()["is_active"]
    assert st1 == 0
    assert st2 == 1


def test_quality_rows_enable_uses_db_ratio(monkeypatch, tmp_path):
    """报表「建议启用」随 enable_ratio 覆盖变化（消除 rules_routes 复制 0.8 失同步）"""
    from app.routes import rules_routes as rr
    _setup(monkeypatch, tmp_path)
    # 正确率 5/6≈0.833 的停用规则：ratio 0.9 不达标、0.8 达标
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold, hit_count, confirmed, is_active) "
            "VALUES ('dim4', 't', '混凝土', 'keyword', 0, 0.6, 6, 5, 0)")
    _set("classify.rule_auto_enable_ratio", 0.9)
    with get_db() as conn:
        assert rr._quality_rows(conn)["suggest_enable"] == []
    _set("classify.rule_auto_enable_ratio", 0.8)
    with get_db() as conn:
        q = rr._quality_rows(conn)
        assert [r["pattern"] for r in q["suggest_enable"]] == ["混凝土"]
