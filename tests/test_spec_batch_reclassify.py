"""批量重新分类测试"""
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path, name="bsr.db"):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)  # 1 spec(现行默认) + 3 clauses 无分类（ai_classified 默认0）


def _get_spec_and_clause(conn):
    spec = conn.execute("SELECT id FROM specifications LIMIT 1").fetchone()
    clause = conn.execute("SELECT id, dim4_specialty FROM clauses LIMIT 1").fetchone()
    return spec["id"], clause


def test_batch_reclassify_unclassified_only(auth_client, monkeypatch, tmp_path):
    """scope=unclassified：只处理未分类/待复核条文，不动已分类"""
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        spec_id, clause = _get_spec_and_clause(conn)
        # 给第 2 条已分类标签
        c2 = conn.execute("SELECT id FROM clauses WHERE id != ? LIMIT 1", (clause["id"],)).fetchone()
        conn.execute("UPDATE clauses SET dim4_specialty='结构专业', ai_classified=1, needs_review=0 WHERE id=?",
                     (c2["id"],))
    # mock process_pending_batches 避免真实 AI
    # 注意：端点内 `from app.ai.classifier_ai import process_pending_batches` 从
    # classifier_ai 模块取绑定，故须 monkeypatch classifier_ai 模块目标而非 spec_routes。
    import app.ai.classifier_ai as ca
    monkeypatch.setattr(ca, "process_pending_batches", lambda force=False: 0)
    resp = auth_client.post("/specs/batch-reclassify",
                            json={"spec_ids": [spec_id], "scope": "unclassified"})
    assert resp.status_code == 200
    with get_db() as conn:
        queued = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE status='pending'"
        ).fetchone()[0]
    # 未分类条文（3条里第2条已分类+needs_review=0 除外）入队；第2条标签保留
    assert queued >= 1
    with get_db() as conn:
        tag = conn.execute("SELECT dim4_specialty FROM clauses WHERE id=?", (c2["id"],)).fetchone()
    assert tag["dim4_specialty"] == "结构专业"  # 未清空


def test_batch_reclassify_scope_all_clears_tags(auth_client, monkeypatch, tmp_path):
    """scope=all：清空已分类标签重入队"""
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        spec_id, clause = _get_spec_and_clause(conn)
        conn.execute("UPDATE clauses SET dim4_specialty='结构专业', ai_classified=1, needs_review=0")
    import app.ai.classifier_ai as ca
    monkeypatch.setattr(ca, "process_pending_batches", lambda force=False: 0)
    resp = auth_client.post("/specs/batch-reclassify",
                            json={"spec_ids": [spec_id], "scope": "all"})
    assert resp.status_code == 200
    with get_db() as conn:
        cleared = conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE dim4_specialty != '' OR dim5_location != '' OR dim6_material != ''"
        ).fetchone()[0]
        queued = conn.execute(
            "SELECT COUNT(*) FROM classification_queue WHERE status='pending'"
        ).fetchone()[0]
    assert cleared == 0  # 标签已清
    assert queued >= 3
