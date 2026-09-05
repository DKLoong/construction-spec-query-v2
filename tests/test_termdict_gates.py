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


# ═══════════════════════════════════════════
# 闸门③：规则沉淀白名单（rule_sink.bump_rule 新建规则分支）
# ═══════════════════════════════════════════

def test_bump_invalid_label_new_rule_forced_inactive(monkeypatch, tmp_path):
    # 修正 brief：须先入一条权威行使词典非空（空词典属 §5.1 放行过渡，另行覆盖），
    # 'style' 才是真正的 ∉ 词典碎片 —— 与同文件闸门①/② invalid 用例同 fixture 口径。
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source) VALUES ('dim6','钢筋','钢筋','manual')")
    invalidate_term_cache()
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        bump_rule(conn, "dim6", "检验", "material",
                  is_confirmed=False, new_rule_active=True, label="style")
    with get_db() as conn:
        r = conn.execute("SELECT is_active, confirmed, label FROM classification_rules").fetchone()
    assert r["is_active"] == 0
    assert r["label"] == "style"
    assert r["confirmed"] == 0


def test_bump_valid_label_respects_active_flag(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source) VALUES ('dim6','钢筋','钢筋','manual')")
    from app.termdict import invalidate_term_cache
    invalidate_term_cache()
    from app.classifier.rule_sink import bump_rule
    with get_db() as conn:
        bump_rule(conn, "dim6", "接头", "material",
                  is_confirmed=False, new_rule_active=True, label="钢筋")
    with get_db() as conn:
        r = conn.execute("SELECT is_active FROM classification_rules").fetchone()
    assert r["is_active"] == 1


# ═══════════════════════════════════════════
# 闸门④：人工确认双写入典（feedback.process_feedback）
# ═══════════════════════════════════════════

def test_confirm_new_label_upserts_term_and_sinks_rule(monkeypatch, tmp_path):
    """词典外新词确认 → 先 upsert 入典(source=review)再沉淀规则（空词典放行路径）。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','规范')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id,clause_no,content) VALUES (?, '1.1', '含 钢筋 的条文内容')",
            (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id,dimension,keyword_score,status)"
            " VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    from app.classifier.feedback import process_feedback
    process_feedback(cid, "dim6", "钢筋", source_conf=0.95)
    with get_db() as conn:
        t = conn.execute("SELECT label, canonical, source FROM term_labels").fetchone()
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        r = conn.execute(
            "SELECT is_active, confirmed, label FROM classification_rules "
            "WHERE pattern='钢筋'").fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
    assert (t["label"], t["canonical"], t["source"]) == ("钢筋", "钢筋", "review")
    assert c["dim6_material"] == "钢筋"
    assert q["status"] == "done"
    assert r["is_active"] == 1       # conf≥0.9 → 新规则启用
    assert r["confirmed"] == 1
    assert r["label"] == "钢筋"


def test_confirm_existing_term_keeps_single_row(monkeypatch, tmp_path):
    """词典已存在同 label 行 → upsert 合并词面，不产生重复行。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source)"
            " VALUES ('dim6','钢筋','螺纹钢','manual')")
        conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','规范')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO clauses (spec_id,clause_no,content) VALUES (?, '1.1', '钢筋 接头')", (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id,dimension,keyword_score,status)"
            " VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    invalidate_term_cache()
    from app.classifier.feedback import process_feedback
    process_feedback(cid, "dim6", "钢筋", source_conf=0.95)
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM term_labels WHERE dimension='dim6' AND label='钢筋'").fetchone()["n"]
    assert n == 1


def test_confirm_new_label_high_conf_active_with_nonempty_dict(monkeypatch, tmp_path):
    """闸门④/I1：非空词典下人工确认高置信新词 → upsert 先入典 → 闸门③不误压 → is_active=1。

    与 test_confirm_new_label_upserts_term_and_sinks_rule 的区别：词典非空（先有 dim6 权威行
    '钢筋'，不含待确认新词 '混凝土'）。若 upsert 未在 bump 前提交，闸门③的 is_valid_label
    走独立连接读不到未提交行 → '混凝土' ∉ 词典 → 误压 is_active=0。
    """
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        # 非空词典：已有 dim6 权威行，但不含待确认的新词 '混凝土'
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source)"
            " VALUES ('dim6','钢筋','钢筋','manual')")
        conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','规范')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id,clause_no,content) VALUES (?, '1.1', '含 混凝土 的条文内容')",
            (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id,dimension,keyword_score,status)"
            " VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    invalidate_term_cache()
    from app.classifier.feedback import process_feedback
    process_feedback(cid, "dim6", "混凝土", source_conf=0.95)
    with get_db() as conn:
        t = conn.execute(
            "SELECT label, canonical, source FROM term_labels "
            "WHERE dimension='dim6' AND label='混凝土'").fetchone()
        r = conn.execute(
            "SELECT is_active, confirmed, label FROM classification_rules "
            "WHERE pattern='混凝土'").fetchone()
    assert t is not None and (t["label"], t["canonical"], t["source"]) == ("混凝土", "混凝土", "review")
    assert r is not None
    assert r["is_active"] == 1       # upsert 先入典使闸门③放行，高置信新规则不误压
    assert r["confirmed"] == 1
    assert r["label"] == "混凝土"


def test_confirm_word_owned_by_other_label_skips_upsert_logs_warn(monkeypatch, tmp_path):
    """闸门④/I1：确认「词面已被同维其它 active label 占用」的新 label → 跳过入典、
    写 WARN 日志、仍写 clauses 列并继续 bump（新规则经闸门③停用），不翻转词典放行模式。

    若照常 upsert，会制造「钢丝」同维双归属（金属 + 钢丝）→ store 一致性校验翻转词典
    「放行」模式、收口静默失效。修复后 term_labels 仍仅「金属」一行，无冲突。
    """
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        # 词典：dim6 权威行 label='金属'，canonical='钢丝'（词面「钢丝」已被占用）
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source)"
            " VALUES ('dim6','金属','钢丝','manual')")
        conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','规范')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id,clause_no,content)"
            " VALUES (?, '1.1', '含 钢丝 的条文内容')", (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id,dimension,keyword_score,status)"
            " VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    invalidate_term_cache()
    from app.classifier.feedback import process_feedback
    process_feedback(cid, "dim6", "钢丝", source_conf=0.95)  # 不得抛异常
    with get_db() as conn:
        labels = [r["label"] for r in conn.execute(
            "SELECT label FROM term_labels WHERE dimension='dim6' ORDER BY id").fetchall()]
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (cid,)).fetchone()
        log = conn.execute(
            "SELECT level, category, action FROM system_logs "
            "WHERE category='review' AND level='WARN'").fetchone()
    assert labels == ["金属"]            # 未新增 '钢丝' 权威行（未制造跨 label 冲突）
    assert c["dim6_material"] == "钢丝"  # clauses 列照常写入
    assert log is not None and log["action"] == "人工确认词面归属冲突，跳过入典"


def test_confirm_sink_failure_logs_error_does_not_raise(monkeypatch, tmp_path):
    """闸门④兜底：事务② bump_rule 抛异常 → 不向调用方抛、写 ERROR 日志、入典与打标已落库。"""
    _db(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO term_labels (dimension,label,canonical,source)"
            " VALUES ('dim6','钢筋','钢筋','manual')")
        conn.execute("INSERT INTO specifications (code,title) VALUES ('GB1','规范')")
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id,clause_no,content) VALUES (?, '1.1', '含 混凝土 的条文内容')",
            (sid,))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id,dimension,keyword_score,status)"
            " VALUES (?, 'dim6', 0.2, 'review')", (cid,))
    invalidate_term_cache()

    import app.classifier.rule_sink as rs

    def boom(*a, **k):
        raise RuntimeError("bump boom")

    monkeypatch.setattr(rs, "bump_rule", boom)

    from app.classifier.feedback import process_feedback
    process_feedback(cid, "dim6", "混凝土", source_conf=0.95)  # 不得向调用方抛异常

    with get_db() as conn:
        t = conn.execute(
            "SELECT label, source FROM term_labels WHERE dimension='dim6' AND label='混凝土'").fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE clause_id=?", (cid,)).fetchone()
        log = conn.execute(
            "SELECT level, action FROM system_logs WHERE action='人工确认规则沉淀失败'").fetchone()
    assert t is not None and t["label"] == "混凝土" and t["source"] == "review"
    assert q["status"] == "done"
    assert log is not None and log["level"] == "ERROR"
