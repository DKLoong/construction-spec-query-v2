import pytest
from app.database import init_db, get_db
from app.classifier import batch_queue
from app.termdict import invalidate_term_cache


def _db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(p))
    init_db()


def _seed(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 1', '规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, '5.1', 'x', '含钢筋的条文')",
        (spec_id,))
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return cid


def _enqueue(conn, clause_id, dimension="dim6"):
    batch_queue.try_enqueue(conn, clause_id, dimension, 0.0)
    conn.execute(
        "UPDATE classification_queue SET batch_id='b1', status='ai_processing' "
        "WHERE clause_id=? AND dimension=?", (clause_id, dimension))


def test_ai_high_conf_invalid_label_downgraded_to_review(monkeypatch, tmp_path):
    """≥0.7 但 label 不在词典 → review：不写列、不沉淀规则。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source) VALUES ('dim6','钢筋','钢筋','manual')")
        cid = _seed(conn)
        _enqueue(conn, cid)
    invalidate_term_cache()
    batch_queue.apply_ai_results("b1", [{"clause_id": cid, "label": "style", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        rules = conn.execute("SELECT COUNT(*) n FROM classification_rules").fetchone()["n"]
    assert q["status"] == "review"
    assert c["dim6_material"] is None
    assert rules == 0


def test_ai_high_conf_valid_label_auto_adopts(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source) VALUES ('dim6','钢筋','钢筋','manual')")
        cid = _seed(conn)
        _enqueue(conn, cid)
    invalidate_term_cache()
    batch_queue.apply_ai_results("b1", [{"clause_id": cid, "label": "钢筋", "confidence": 0.9}])
    with get_db() as conn:
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
    assert q["status"] == "auto_adopted"
    assert c["dim6_material"] == "钢筋"


# ═══════════════════════════════════════════
# 闸门①：导入写库 label 白名单（import_routes 条文分类主循环）
# ═══════════════════════════════════════════

def _process_fake_import(monkeypatch, tmp_path, md_text, code):
    """驱动 import_routes._process_import 的 .md 直通路径，真实跑 Phase2 分类主循环。

    说明：PDF 路径只到 review_needed（等人工确认），不会进条文分类循环；此处用 .md
    让 _process_import 直接进 _process_import_phase2（单线程同步）。VectorStore 置 None
    跳过向量索引（与分类/落库断言无关），OUTPUT_DIR 指到 tmp 避免写 data/。
    """
    from app.routes import import_routes

    task_id = "task-g1"
    md = tmp_path / "raw.md"
    md.write_text(md_text, encoding="utf-8")
    import_routes.progress_store[task_id] = {
        "status": "uploading", "progress": 0, "message": "",
    }
    monkeypatch.setattr(import_routes, "VectorStore", lambda: None)
    monkeypatch.setattr(import_routes, "OUTPUT_DIR", str(tmp_path / "outputs"))
    import_routes._process_import(task_id, str(md), "标题", code, force_ocr=False)


def test_rule_hit_invalid_label_goes_review_not_column(monkeypatch, tmp_path):
    """闸门①：规则命中 label ∉ 词典（碎片）→ 不写 dim 列、不累 hit/confirmed、入队待审。"""
    db_path = tmp_path / "t1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        # 碎片规则：label='结构'，词典 dim4 无该权威行（仅存「钢筋」）→ 应被拦
        conn.execute(
            "INSERT INTO classification_rules (dimension, pattern, threshold, label, is_active)"
            " VALUES ('dim4', '钢筋', 0.4, '结构', 1)")
        conn.execute(
            "INSERT INTO term_labels (dimension, label, canonical, source)"
            " VALUES ('dim4', '钢筋', '钢筋', 'manual')")
    invalidate_term_cache()
    _process_fake_import(monkeypatch, tmp_path, "# 规范\n\n5.1.1 钢筋钢筋 检验。", "JGJ 107")
    with get_db() as conn:
        clause = conn.execute("SELECT dim4_specialty FROM clauses ORDER BY id LIMIT 1").fetchone()
        q = conn.execute(
            "SELECT status, dimension FROM classification_queue ORDER BY id LIMIT 1").fetchone()
        rule = conn.execute("SELECT hit_count, confirmed FROM classification_rules LIMIT 1").fetchone()
    assert (clause["dim4_specialty"] or "") == ""
    assert q is not None and q["dimension"] == "dim4" and q["status"] == "pending"
    assert rule["hit_count"] == 0
    assert rule["confirmed"] == 0


def test_rule_hit_valid_label_writes_column(monkeypatch, tmp_path):
    """闸门①：命中 label ∈ 词典 → 照旧写列 + 规则 confirmed。"""
    db_path = tmp_path / "t2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules (dimension, pattern, threshold, label, is_active)"
            " VALUES ('dim4', '钢筋', 0.4, '结构', 1)")
        conn.execute(
            "INSERT INTO term_labels (dimension, label, canonical, source)"
            " VALUES ('dim4', '结构', '结构', 'manual')")
    invalidate_term_cache()
    _process_fake_import(monkeypatch, tmp_path, "# 规范\n\n5.1.1 钢筋钢筋 检验。", "JGJ 107")
    with get_db() as conn:
        clause = conn.execute("SELECT dim4_specialty FROM clauses ORDER BY id LIMIT 1").fetchone()
        rule = conn.execute("SELECT confirmed FROM classification_rules LIMIT 1").fetchone()
    assert clause["dim4_specialty"] == "结构"
    assert rule["confirmed"] == 1
