"""分类管理路由测试"""
import pytest


# ===== 规则列表测试 =====

def test_rules_list_page_returns_200(auth_client):
    """规则管理页需要鉴权"""
    resp = auth_client.get("/rules")
    assert resp.status_code == 200


def test_rules_list_returns_html(auth_client, monkeypatch, tmp_path):
    """规则列表返回 HTML 片段"""
    db_path = tmp_path / "test_rules.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db

    init_db()
    # 写入种子规则
    import sys
    from pathlib import Path as _Path
    _proj = _Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_proj / "scripts"))
    import seed_rules
    seed_rules.seed(conn=None)

    resp = auth_client.get("/rules/list")
    assert resp.status_code == 200
    # rules table should contain rule data — check for keyword match_type as indicator
    assert "keyword" in resp.text or "所属专业" in resp.text or "dim4" in resp.text


def test_rules_list_filter_by_dimension(auth_client, monkeypatch, tmp_path):
    """支持按维度筛选规则"""
    db_path = tmp_path / "test_rules_dim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db

    init_db()
    import sys
    from pathlib import Path as _Path
    _proj = _Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_proj / "scripts"))
    import seed_rules
    seed_rules.seed(conn=None)

    resp = auth_client.get("/rules/list?dimension=dim4")
    assert resp.status_code == 200
    # dim4 规则应该包含 "结构" 等关键词
    assert "结构" in resp.text or "给排水" in resp.text or "电气" in resp.text


# ===== 规则 CRUD 测试 =====

def test_create_rule(auth_client, monkeypatch, tmp_path):
    """创建新规则"""
    db_path = tmp_path / "test_create_rule.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()

    resp = auth_client.post("/rules/create", data={
        "dimension": "dim4",
        "sub_field": "specialty",
        "pattern": "测试规则关键词",
        "match_type": "keyword",
        "priority": 1,
        "threshold": 0.6,
    })
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE pattern = ?",
            ("测试规则关键词",),
        ).fetchone()
    assert rule is not None
    assert rule["dimension"] == "dim4"
    assert rule["is_active"] == 1


def test_toggle_rule(auth_client, monkeypatch, tmp_path):
    """启用/禁用规则"""
    db_path = tmp_path / "test_toggle.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules (dimension, pattern, threshold) VALUES (?, ?, ?)",
            ("dim4", "toggle_test", 0.5),
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 禁用
    resp = auth_client.post(f"/rules/{rule_id}/toggle")
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute("SELECT is_active FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()
    assert rule["is_active"] == 0

    # 重新启用
    resp = auth_client.post(f"/rules/{rule_id}/toggle")
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute("SELECT is_active FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()
    assert rule["is_active"] == 1


def test_delete_rule(auth_client, monkeypatch, tmp_path):
    """删除规则"""
    db_path = tmp_path / "test_delete.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules (dimension, pattern, threshold) VALUES (?, ?, ?)",
            ("dim5", "delete_test", 0.5),
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.delete(f"/rules/{rule_id}")
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute("SELECT * FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()
    assert rule is None


# ===== 审核队列测试 =====

def test_review_page_returns_200(auth_client):
    """审核页需要鉴权"""
    resp = auth_client.get("/review")
    assert resp.status_code == 200


def test_review_list_shows_items(auth_client, monkeypatch, tmp_path):
    """审核列表显示待审核项"""
    db_path = tmp_path / "test_review.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title) VALUES (?, ?)",
            ("GB-TEST", "测试规范"),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, needs_review) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.1", "测试条文", "混凝土施工", 1),
        )
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score, ai_label, ai_confidence, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (clause_id, "dim6", 0.3, "混凝土", 0.85, "review"),
        )

    resp = auth_client.get("/review/list")
    assert resp.status_code == 200
    # 应该包含条文内容
    assert "混凝土施工" in resp.text


def test_confirm_review_triggers_feedback(auth_client, monkeypatch, tmp_path):
    """确认 AI 标签触发反馈闭环"""
    db_path = tmp_path / "test_confirm.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title) VALUES (?, ?)",
            ("GB-TEST2", "测试规范2"),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, needs_review) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.1", "钢筋条文", "钢筋绑扎应牢固可靠", 1),
        )
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score, ai_label, ai_confidence, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (clause_id, "dim6", 0.3, "金属材料", 0.85, "review"),
        )
        queue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.post(f"/review/{queue_id}/confirm")
    assert resp.status_code == 200

    with get_db() as conn:
        # 队列状态应变为 done
        queue = conn.execute(
            "SELECT status FROM classification_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        assert queue["status"] == "done"

        # clauses 应更新 dim6
        clause = conn.execute(
            "SELECT dim6_material, ai_classified, needs_review FROM clauses WHERE id = ?",
            (clause_id,),
        ).fetchone()
        assert clause["dim6_material"] == "金属材料"
        assert clause["ai_classified"] == 1
        assert clause["needs_review"] == 0

        # 反馈应创建关键词规则
        rules = conn.execute(
            "SELECT * FROM classification_rules WHERE dimension = ? AND pattern IN (?, ?, ?, ?)",
            ("dim6", "钢筋绑扎", "钢筋绑", "钢筋", "绑扎"),
        ).fetchone()
        assert rules is not None, f"确认后应自动提取关键词创建规则，现有规则: {[dict(r) for r in conn.execute('SELECT pattern FROM classification_rules WHERE dimension=?', ('dim6',)).fetchall()]}"


def test_reject_review_clears_label(auth_client, monkeypatch, tmp_path):
    """驳回 AI 标签"""
    db_path = tmp_path / "test_reject.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title) VALUES (?, ?)",
            ("GB-TEST3", "测试规范3"),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, needs_review) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "3.1", "测试", "测试内容", 1),
        )
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score, ai_label, ai_confidence, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (clause_id, "dim6", 0.2, "错误标签", 0.55, "review"),
        )
        queue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.post(f"/review/{queue_id}/reject")
    assert resp.status_code == 200

    with get_db() as conn:
        queue = conn.execute(
            "SELECT status, ai_label FROM classification_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        assert queue["status"] == "rejected"
        assert queue["ai_label"] is None

        clause = conn.execute(
            "SELECT needs_review FROM clauses WHERE id = ?", (clause_id,)
        ).fetchone()
        assert clause["needs_review"] == 0


# ===== 队列统计测试 =====

def test_queue_stats(auth_client, monkeypatch, tmp_path):
    """队列统计返回 JSON"""
    db_path = tmp_path / "test_stats.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title) VALUES (?, ?)",
            ("GB-S", "统计测试"),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in range(5):
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
                (spec_id, f"{i}.1", f"内容{i}"),
            )
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO classification_queue (clause_id, dimension, keyword_score, status) "
                "VALUES (?, ?, ?, ?)",
                (cid, "dim4", 0.3, "pending"),
            )

    resp = auth_client.get("/queue/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "dim4" in data
    assert data["dim4"]["pending"] == 5
    assert data["dim4"]["total"] == 5
