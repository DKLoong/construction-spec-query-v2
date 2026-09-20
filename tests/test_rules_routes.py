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
    # 低置信块不再提供候选词勾选背书（C12：确认=纯打标，词面沉淀仅 Tab2）
    assert "候选词（勾选背书）" not in resp.text and "kw-check" not in resp.text


def test_confirm_review_pure_tagging(auth_client, monkeypatch, tmp_path):
    """确认 AI 标签 = 纯打标：写列 + queue done，不沉淀词面（C12）"""
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

    resp = auth_client.post(f"/review/{queue_id}/confirm",
                            json={"patterns": ["钢筋"]})
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

        # 纯打标：不沉淀任何规则（即便请求体带了 patterns，词面沉淀仅 Tab2）
        n_rules = conn.execute(
            "SELECT COUNT(*) n FROM classification_rules WHERE dimension='dim6'").fetchone()["n"]
        assert n_rules == 0, f"低置信确认不应创建规则，现有规则: {[dict(r) for r in conn.execute('SELECT pattern FROM classification_rules WHERE dimension=?', ('dim6',)).fetchall()]}"


def test_confirm_review_guard_non_review_noop(auth_client, monkeypatch, tmp_path):
    """C15：已定案（status='done'）队列项在 confirm/reject 端点均 no-op，不覆写人工定案。

    断言端点自身守卫（而非仅 process_feedback 内层 EXISTS）：若 rules_routes 的
    `AND status='review'` 被删，本行会走 INFO「确认分类标签」分支 → 下方日志断言失败。
    """
    db_path = tmp_path / "test_guard.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-G1', '守卫规范')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content, dim6_material, needs_review) "
            "VALUES (?, '1.1', '守卫条文', '混凝土', 0)", (spec_id,))
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO classification_queue (clause_id, dimension, keyword_score, ai_label, "
            "ai_confidence, status) VALUES (?, 'dim6', 0.2, '金属材料', 0.55, 'done')",
            (clause_id,))
        queue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.post(f"/review/{queue_id}/confirm", json={})
    assert resp.status_code == 200
    resp = auth_client.post(f"/review/{queue_id}/reject")
    assert resp.status_code == 200

    with get_db() as conn:
        c = conn.execute("SELECT dim6_material FROM clauses WHERE id=?", (clause_id,)).fetchone()
        q = conn.execute("SELECT status FROM classification_queue WHERE id=?", (queue_id,)).fetchone()
        skipped = conn.execute(
            "SELECT COUNT(*) n FROM system_logs WHERE category='review' "
            "AND action='确认跳过-非待审状态' AND level='WARN'").fetchone()["n"]
        confirmed = conn.execute(
            "SELECT COUNT(*) n FROM system_logs WHERE category='review' "
            "AND action='确认分类标签'").fetchone()["n"]
    assert c["dim6_material"] == "混凝土"   # 人工定案不被覆写
    assert q["status"] == "done"           # 已定案不被驳回重开
    assert skipped == 1 and confirmed == 0  # 端点守卫命中：记「跳过」而非「确认成功」


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


# ===== 子字段自动补全测试 =====

def test_sub_fields_returns_unique_list(auth_client, monkeypatch, tmp_path):
    """获取某维度下已有的子字段列表"""
    db_path = tmp_path / "test_subfields.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules (dimension, sub_field, pattern, threshold) VALUES "
            "('dim4', 'specialty', '结构', 0.5),"
            "('dim4', 'specialty', '给排水', 0.5),"
            "('dim4', 'specialty', '暖通', 0.5),"
            "('dim4', 'material', '混凝土', 0.5),"
            "('dim5', 'location', '基础', 0.5)"
        )

    resp = auth_client.get("/rules/sub-fields?dimension=dim4")
    assert resp.status_code == 200
    data = resp.json()
    # 应该包含 specialty 和 material，且不重复
    assert "specialty" in data
    assert "material" in data
    assert len(data) == 2  # 去重后只有 2 个


def test_sub_fields_empty_for_no_match(auth_client, monkeypatch, tmp_path):
    """无匹配维度时返回空列表"""
    db_path = tmp_path / "test_subfields_empty.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db

    init_db()

    resp = auth_client.get("/rules/sub-fields?dimension=dim1")
    assert resp.status_code == 200
    data = resp.json()
    assert data == []


def test_sub_fields_requires_auth(client):
    """未登录不能访问子字段接口"""
    resp = client.get("/rules/sub-fields?dimension=dim4", follow_redirects=False)
    assert resp.status_code == 302


# ===== 规则编辑测试 =====

def test_update_rule_all_fields(auth_client, monkeypatch, tmp_path):
    """编辑规则所有字段"""
    db_path = tmp_path / "test_update_rule.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules "
            "(dimension, sub_field, pattern, match_type, priority, threshold) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("dim4", "specialty", "旧关键词", "keyword", 1, 0.5),
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.put(f"/rules/{rule_id}", data={
        "dimension": "dim5",
        "sub_field": "location",
        "pattern": "新关键词",
        "match_type": "exact",
        "priority": 5,
        "threshold": 0.8,
    })
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()
    assert rule["dimension"] == "dim5"
    assert rule["sub_field"] == "location"
    assert rule["pattern"] == "新关键词"
    assert rule["match_type"] == "exact"
    assert rule["priority"] == 5
    assert rule["threshold"] == 0.8


def test_update_rule_partial(auth_client, monkeypatch, tmp_path):
    """更新规则（可只改部分字段，未传字段保持默认值）"""
    db_path = tmp_path / "test_update_partial.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules "
            "(dimension, sub_field, pattern, match_type, priority, threshold) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("dim6", "material", "原关键词", "keyword", 3, 0.7),
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 只改 pattern 和 threshold，其他字段传原值
    resp = auth_client.put(f"/rules/{rule_id}", data={
        "dimension": "dim6",
        "sub_field": "material",
        "pattern": "改动关键词",
        "match_type": "keyword",
        "priority": 3,
        "threshold": 0.9,
    })
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()
    assert rule["pattern"] == "改动关键词"
    assert rule["threshold"] == 0.9
    assert rule["dimension"] == "dim6"


def test_update_rule_not_found(auth_client, monkeypatch, tmp_path):
    """编辑不存在的规则返回 404"""
    db_path = tmp_path / "test_update_notfound.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.put("/rules/99999", data={"dimension": "dim4", "pattern": "x"})
    assert resp.status_code == 404


def test_update_rule_requires_auth(client):
    """未登录不能编辑规则"""
    resp = client.put("/rules/1", data={"dimension": "dim4", "pattern": "x"},
                      follow_redirects=False)
    assert resp.status_code == 302


# ── label（赋值标签）人工可维护：规则页原先不支持 label，人工只能建「词即标签」规则，
#    「特征词→标签」是 AI 沉淀路径专属。以下用例锁定 label 的人工编辑能力。──

def test_create_rule_with_label(auth_client, monkeypatch, tmp_path):
    """新建规则可指定 label（特征词 → 标签）"""
    db_path = tmp_path / "test_create_label.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()

    resp = auth_client.post("/rules/create", data={
        "dimension": "dim6",
        "sub_field": "material",
        "pattern": "丝头",
        "match_type": "keyword",
        "priority": 1,
        "threshold": 0.6,
        "label": "钢筋",
    })
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE pattern = ?", ("丝头",)
        ).fetchone()
    assert rule["label"] == "钢筋"


def test_create_rule_without_label_stores_null(auth_client, monkeypatch, tmp_path):
    """不传 label 时存 NULL（旧语义：词即标签），不得存空字符串"""
    db_path = tmp_path / "test_create_nolabel.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()

    resp = auth_client.post("/rules/create", data={
        "dimension": "dim6",
        "sub_field": "material",
        "pattern": "砌体",
        "match_type": "keyword",
        "priority": 1,
        "threshold": 0.6,
        "label": "   ",  # 仅空白 → 视同未填
    })
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute(
            "SELECT * FROM classification_rules WHERE pattern = ?", ("砌体",)
        ).fetchone()
    assert rule["label"] is None


def test_update_rule_changes_label(auth_client, monkeypatch, tmp_path):
    """编辑规则可改 label —— 「给同一标签配多个关键词」的基础能力"""
    db_path = tmp_path / "test_update_label.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules "
            "(dimension, sub_field, pattern, match_type, priority, threshold, label) "
            "VALUES ('dim6', 'material', '混凝土', 'keyword', 1, 0.5, NULL)"
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.put(f"/rules/{rule_id}", data={
        "dimension": "dim6",
        "sub_field": "material",
        "pattern": "混凝土",
        "match_type": "keyword",
        "priority": 1,
        "threshold": 0.5,
        "label": "现浇混凝土",
    })
    assert resp.status_code == 200

    with get_db() as conn:
        rule = conn.execute(
            "SELECT label FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()
    assert rule["label"] == "现浇混凝土"


def test_same_label_multiple_patterns(auth_client, monkeypatch, tmp_path):
    """同一标签可挂多个关键词（多条规则共享 label），且列表页显示该标签"""
    db_path = tmp_path / "test_multi_pattern.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    for pat in ("接头", "丝头", "套筒"):
        auth_client.post("/rules/create", data={
            "dimension": "dim6", "sub_field": "material", "pattern": pat,
            "match_type": "keyword", "priority": 1, "threshold": 0.6,
            "label": "钢筋",
        })

    with get_db() as conn:
        rules = conn.execute(
            "SELECT pattern, label FROM classification_rules WHERE label = '钢筋' ORDER BY pattern"
        ).fetchall()
    assert [r["pattern"] for r in rules] == ["丝头", "套筒", "接头"]

    html = auth_client.get("/rules/list").text
    assert "标签" in html                      # 列表含标签列
    for pat in ("接头", "丝头", "套筒"):
        assert pat in html
    assert html.count("钢筋") >= 3             # 三个关键词都显示同一标签


def test_update_rule_response_reflects_new_label(auth_client, monkeypatch, tmp_path):
    """PUT 返回的行片段必须反映新 label —— 否则前端替换行后「标签」列仍显示旧值

    原缺陷：响应由 UPDATE 之前查到的行 dict 叠加 7 个字段拼成，唯独漏了 label，
    于是 DB 更新成功但返回片段陈旧，前端 htmx/fetch 替换行后标签列不更新。
    """
    db_path = tmp_path / "test_update_label_resp.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules "
            "(dimension, sub_field, pattern, match_type, priority, threshold, label) "
            "VALUES ('dim6', 'material', '接头', 'keyword', 1, 0.5, '旧标签')"
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.put(f"/rules/{rule_id}", data={
        "dimension": "dim6",
        "sub_field": "material",
        "pattern": "接头",
        "match_type": "keyword",
        "priority": 1,
        "threshold": 0.5,
        "label": "新标签",
    })
    assert resp.status_code == 200
    assert "新标签" in resp.text, "响应片段未反映新 label"
    assert "旧标签" not in resp.text, "响应片段残留旧 label"


def test_update_rule_response_reflects_cleared_label(auth_client, monkeypatch, tmp_path):
    """清空 label 后响应片段也不得残留旧标签（占位「词即标签」应出现）"""
    db_path = tmp_path / "test_update_label_clear.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules "
            "(dimension, sub_field, pattern, match_type, priority, threshold, label) "
            "VALUES ('dim6', 'material', '砌体', 'keyword', 1, 0.5, '待清标签')"
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.put(f"/rules/{rule_id}", data={
        "dimension": "dim6", "sub_field": "material", "pattern": "砌体",
        "match_type": "keyword", "priority": 1, "threshold": 0.5, "label": "",
    })
    assert resp.status_code == 200
    assert "待清标签" not in resp.text
    assert "词即标签" in resp.text


# ── T9 按标签分组视图（/rules/grouped）──
# 分组键 = (维度, 有效标签)；label 为空归入该维「词即标签」组（旧语义：pattern 即标签）。

def _insert_rule(conn, dimension, pattern, label=None, hit=0, confirmed=0, priority=0):
    conn.execute(
        """INSERT INTO classification_rules
           (dimension, sub_field, pattern, match_type, priority, threshold,
            hit_count, confirmed, is_active, label)
           VALUES (?, 'x', ?, 'keyword', ?, 0.5, ?, ?, 1, ?)""",
        (dimension, pattern, priority, hit, confirmed, label),
    )


def _grouped_db(auth_client, monkeypatch, tmp_path, name):
    db_path = tmp_path / f"{name}.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    return get_db


def test_rules_grouped_groups_by_label_with_counts_and_hits(
        auth_client, monkeypatch, tmp_path):
    """同标签的多条关键词聚成一组，并给出关键词数与命中合计"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "grp_basic")
    with get_db() as conn:
        _insert_rule(conn, "dim6", "接头", "钢筋", hit=2)
        _insert_rule(conn, "dim6", "丝头", "钢筋", hit=3)
        _insert_rule(conn, "dim6", "套筒", "钢筋", hit=4)

    resp = auth_client.get("/rules/grouped")
    assert resp.status_code == 200
    # 属性顺序固定，整串断言以保证计数/命中合计归属于该组
    assert ('data-dimension="dim6" data-label="钢筋" data-count="3" data-hits="9"'
            in resp.text)
    for pat in ("接头", "丝头", "套筒"):
        assert pat in resp.text


def test_rules_grouped_label_less_grouped_per_dimension(
        auth_client, monkeypatch, tmp_path):
    """未设标签的规则按维度各自成组，不混成一个全局组"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "grp_nolabel")
    with get_db() as conn:
        _insert_rule(conn, "dim4", "结构")
        _insert_rule(conn, "dim6", "砌体")

    html = auth_client.get("/rules/grouped").text
    assert 'data-dimension="dim4" data-label="" data-count="1"' in html
    assert 'data-dimension="dim6" data-label="" data-count="1"' in html
    assert "词即标签" in html


def test_rules_grouped_collapse_default_labeled_open_others_closed(
        auth_client, monkeypatch, tmp_path):
    """默认折叠态：有标签组展开（可读），未设标签组折叠（词表量大）"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "grp_collapse")
    with get_db() as conn:
        _insert_rule(conn, "dim6", "接头", "钢筋")
        _insert_rule(conn, "dim4", "结构")

    html = auth_client.get("/rules/grouped").text
    assert 'data-label="钢筋" data-count="1" data-hits="0" data-default-open="true"' in html
    assert 'data-label="" data-count="1" data-hits="0" data-default-open="false"' in html


def test_rules_grouped_dimension_filter(auth_client, monkeypatch, tmp_path):
    """?dimension= 只返回该维度的组"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "grp_filter")
    with get_db() as conn:
        _insert_rule(conn, "dim4", "结构")
        _insert_rule(conn, "dim6", "接头", "钢筋")

    html = auth_client.get("/rules/grouped?dimension=dim6").text
    assert 'data-dimension="dim6"' in html
    assert 'data-dimension="dim4"' not in html


def test_rules_grouped_header_offers_add_keyword_prefilled(
        auth_client, monkeypatch, tmp_path):
    """组头「+ 添加关键词」须把该组的维度与标签预填进新建弹窗"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "grp_add")
    with get_db() as conn:
        _insert_rule(conn, "dim6", "接头", "钢筋")

    html = auth_client.get("/rules/grouped").text
    assert "+ 添加关键词" in html
    assert "openCreateFor('dim6', '钢筋')" in html


def test_rules_grouped_requires_auth(client):
    """未登录不能访问分组视图"""
    resp = client.get("/rules/grouped", follow_redirects=False)
    assert resp.status_code == 302


def test_rules_list_shows_label_or_placeholder(auth_client, monkeypatch, tmp_path):
    """label 为空时列表显示「词即标签」占位，不显示空白"""
    db_path = tmp_path / "test_label_placeholder.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO classification_rules "
            "(dimension, sub_field, pattern, match_type, priority, threshold, label) "
            "VALUES ('dim4', 'specialty', '屋面', 'keyword', 1, 0.5, NULL)"
        )

    html = auth_client.get("/rules/list").text
    assert "词即标签" in html


# ── 规则身份唯一键 (dimension, pattern)：同维同词只应有一条规则 ──
# 语义依据：classify_clause 同维只取最高分那一条（重复者沦为死配置）；
# bump_rule 按 (dimension, pattern) 定位规则（重复时命中统计落到不确定的行，
# 而规则自动启停正是按 confirmed/hit_count 的正确率算的，因而被污染）。
# 跨维度同词合法（钢筋 在 dim5/dim6 含义不同），不得拦。

def _mk_rule(client, **over):
    data = {"dimension": "dim6", "sub_field": "material", "pattern": "翻模",
            "match_type": "keyword", "priority": 0, "threshold": 0.6, "label": ""}
    data.update(over)
    return client.post("/rules/create", data=data)


def test_create_rule_duplicate_same_dimension_blocked(auth_client, monkeypatch, tmp_path):
    """同维度同关键词重复新建 → 400，且不落行"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "dup_block")
    with get_db() as conn:
        _insert_rule(conn, "dim6", "翻模", "翻模")

    resp = _mk_rule(auth_client)
    assert resp.status_code == 400
    body = resp.json()
    assert "翻模" in body["detail"]
    # 冲突信息须含既有规则全字段，供前端「改为编辑该规则」直接打开
    conflict = body["conflict"]
    assert conflict["pattern"] == "翻模"
    assert conflict["label"] == "翻模"
    assert conflict["dimension"] == "dim6"
    for k in ("id", "sub_field", "match_type", "priority", "threshold", "is_active"):
        assert k in conflict, f"冲突信息缺字段 {k}"

    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM classification_rules WHERE pattern='翻模'").fetchone()[0]
    assert n == 1, "被拦下后不应新增行"


def test_create_rule_same_pattern_other_dimension_allowed(auth_client, monkeypatch, tmp_path):
    """跨维度同词合法（钢筋 在 dim5/dim6 含义不同）→ 不得拦"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "dup_crossdim")
    with get_db() as conn:
        _insert_rule(conn, "dim5", "钢筋", "主体结构")

    resp = _mk_rule(auth_client, dimension="dim6", pattern="钢筋")
    assert resp.status_code == 200
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM classification_rules WHERE pattern='钢筋'").fetchone()[0]
    assert n == 2


def test_create_rule_pattern_whitespace_trimmed(auth_client, monkeypatch, tmp_path):
    """关键词前后空白须裁掉 —— 否则 ' 翻模' 能绕过判重、也会导致匹配不上"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "dup_trim")
    with get_db() as conn:
        _insert_rule(conn, "dim6", "翻模", "翻模")

    resp = _mk_rule(auth_client, pattern="  翻模  ")
    assert resp.status_code == 400, "' 翻模' 应被判为与 '翻模' 重复"
    with get_db() as conn:
        rows = conn.execute(
            "SELECT pattern FROM classification_rules WHERE dimension='dim6'").fetchall()
    assert [r["pattern"] for r in rows] == ["翻模"]


def test_update_rule_pattern_collision_blocked(auth_client, monkeypatch, tmp_path):
    """编辑时把关键词改成同维已存在的 → 400（否则编辑路径可绕过判重）"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "dup_update")
    with get_db() as conn:
        _insert_rule(conn, "dim6", "接头", "钢筋")
        _insert_rule(conn, "dim6", "丝头", "钢筋")
        other_id = conn.execute(
            "SELECT id FROM classification_rules WHERE pattern='丝头'").fetchone()[0]

    resp = auth_client.put(f"/rules/{other_id}", data={
        "dimension": "dim6", "sub_field": "material", "pattern": "接头",
        "match_type": "keyword", "priority": 0, "threshold": 0.6, "label": "钢筋",
    })
    assert resp.status_code == 400
    assert "接头" in resp.json()["detail"]
    with get_db() as conn:
        kept = conn.execute(
            "SELECT pattern FROM classification_rules WHERE id = ?", (other_id,)).fetchone()
    assert kept["pattern"] == "丝头", "被拦下后不应改动"


def test_update_rule_keeping_own_pattern_allowed(auth_client, monkeypatch, tmp_path):
    """只改阈值/标签、关键词不变 → 200（判重必须排除自身）"""
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "dup_self")
    with get_db() as conn:
        _insert_rule(conn, "dim6", "接头", "钢筋")
        rid = conn.execute("SELECT id FROM classification_rules").fetchone()[0]

    resp = auth_client.put(f"/rules/{rid}", data={
        "dimension": "dim6", "sub_field": "material", "pattern": "接头",
        "match_type": "keyword", "priority": 3, "threshold": 0.8, "label": "钢筋",
    })
    assert resp.status_code == 200
    with get_db() as conn:
        r = conn.execute("SELECT threshold, priority FROM classification_rules WHERE id = ?",
                         (rid,)).fetchone()
    assert r["threshold"] == 0.8 and r["priority"] == 3


def test_rule_unique_index_created_on_init(auth_client, monkeypatch, tmp_path):
    """init_db 须建 (dimension, pattern) 唯一索引（权威兜底）"""
    import sqlite3
    import pytest
    get_db = _grouped_db(auth_client, monkeypatch, tmp_path, "dup_index")
    with get_db() as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='classification_rules'")]
    assert "uq_rule_dim_pattern" in names

    with get_db() as conn:
        _insert_rule(conn, "dim6", "翻模", "翻模")
    with pytest.raises(sqlite3.IntegrityError):
        with get_db() as conn:
            _insert_rule(conn, "dim6", "翻模")


def test_init_db_skips_index_when_duplicates_exist(tmp_path, monkeypatch):
    """库内已有重复时，建索引须跳过并告警，绝不静默删数据"""
    import sqlite3
    db = tmp_path / "dup_pre.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db))
    with sqlite3.connect(db) as c:
        c.execute("""CREATE TABLE classification_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT NOT NULL,
            sub_field TEXT, pattern TEXT NOT NULL, match_type TEXT DEFAULT 'keyword',
            priority INTEGER DEFAULT 0, threshold REAL NOT NULL,
            hit_count INTEGER DEFAULT 0, confirmed INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1, label TEXT, locked INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')))""")
        c.executemany(
            "INSERT INTO classification_rules (dimension, pattern, threshold) VALUES (?,?,0.5)",
            [("dim6", "翻模"), ("dim6", "翻模")])

    from app.database import init_db
    init_db()  # 不得抛错

    with sqlite3.connect(db) as c:
        names = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_rule_dim_pattern'")]
        n = c.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0]
    assert names == [], "有重复时不应建索引"
    assert n == 2, "绝不能在迁移里静默删除数据"
