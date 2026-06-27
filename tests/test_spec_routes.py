"""规范管理与条文 CRUD 路由测试"""
import pytest


def _setup_spec_data(conn):
    """写入测试规范与条文"""
    conn.execute(
        "INSERT INTO specifications (code, title) VALUES (?, ?)",
        ("GB 50204", "混凝土规范"),
    )
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i in range(3):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, f"{i+1}.1", f"条文{i+1}", f"第{i+1}条内容测试"),
        )
    conn.execute("UPDATE specifications SET clause_count = 3 WHERE id = ?", (spec_id,))
    return spec_id


# ═══════════════════════════════════════════
# 规范列表
# ═══════════════════════════════════════════

def test_specs_page_returns_200(auth_client):
    """规范管理页需要鉴权"""
    resp = auth_client.get("/specs")
    assert resp.status_code == 200


def test_specs_list_empty(auth_client, monkeypatch, tmp_path):
    """空数据库返回空列表"""
    db_path = tmp_path / "test_specs_empty.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()
    resp = auth_client.get("/specs/list")
    assert resp.status_code == 200


def test_specs_list_with_data(auth_client, monkeypatch, tmp_path):
    """有规范时显示列表"""
    db_path = tmp_path / "test_specs_data.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_spec_data(conn)
    resp = auth_client.get("/specs/list")
    assert resp.status_code == 200
    assert "GB 50204" in resp.text


# ═══════════════════════════════════════════
# 删除规范
# ═══════════════════════════════════════════

def test_delete_spec_cascades(auth_client, monkeypatch, tmp_path):
    """删除规范级联删除条文和队列"""
    db_path = tmp_path / "test_delete_spec.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    # Mock VectorStore 避免加载模型
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.__init__", lambda self: None,
    )
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.delete_clause", lambda self, x: None,
    )
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM clauses WHERE spec_id = ?", (spec_id,)
            ).fetchall()
        ]
        # 添加队列项
        for cid in clause_ids[:2]:
            conn.execute(
                "INSERT INTO classification_queue (clause_id, dimension, keyword_score) VALUES (?, ?, ?)",
                (cid, "dim6", 0.3),
            )

    resp = auth_client.delete(f"/specs/{spec_id}")
    assert resp.status_code == 200

    with get_db() as conn:
        spec = conn.execute(
            "SELECT id FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()
        assert spec is None
        clauses = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ?", (spec_id,)
        ).fetchall()
        assert len(clauses) == 0
        queue = conn.execute(
            "SELECT id FROM classification_queue WHERE clause_id IN ({})".format(
                ",".join("?" * len(clause_ids))
            ), clause_ids,
        ).fetchall()
        assert len(queue) == 0


# ═══════════════════════════════════════════
# 条文列表
# ═══════════════════════════════════════════

def test_clauses_list(auth_client, monkeypatch, tmp_path):
    """条文列表显示规范下的所有条文"""
    db_path = tmp_path / "test_clauses_list.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)

    resp = auth_client.get(f"/specs/{spec_id}/clauses")
    assert resp.status_code == 200
    assert "条文1" in resp.text
    assert "第1条内容测试" in resp.text


# ═══════════════════════════════════════════
# 条文编辑
# ═══════════════════════════════════════════

def test_edit_clause_form(auth_client, monkeypatch, tmp_path):
    """编辑表单返回 HTML"""
    db_path = tmp_path / "test_edit_form.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.get(f"/specs/{spec_id}/clauses/{clause['id']}/edit")
    assert resp.status_code == 200


def test_update_clause(auth_client, monkeypatch, tmp_path):
    """更新条文内容"""
    db_path = tmp_path / "test_update_clause.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.__init__", lambda self: None,
    )
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.index_clause",
        lambda self, a, b, c, d="": None,
    )
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.put(
        f"/specs/{spec_id}/clauses/{clause['id']}",
        data={"clause_no": "99.9", "title": "新标题", "content": "更新后的内容"},
    )
    assert resp.status_code == 200

    with get_db() as conn:
        updated = conn.execute(
            "SELECT * FROM clauses WHERE id = ?", (clause["id"],)
        ).fetchone()
    assert updated["clause_no"] == "99.9"
    assert updated["title"] == "新标题"
    assert updated["content"] == "更新后的内容"


# ═══════════════════════════════════════════
# 删除条文
# ═══════════════════════════════════════════

def test_delete_clause(auth_client, monkeypatch, tmp_path):
    """删除单条条文"""
    db_path = tmp_path / "test_delete_clause.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.__init__", lambda self: None,
    )
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.delete_clause", lambda self, x: None,
    )
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.delete(f"/specs/{spec_id}/clauses/{clause['id']}")
    assert resp.status_code == 200

    with get_db() as conn:
        deleted = conn.execute(
            "SELECT id FROM clauses WHERE id = ?", (clause["id"],)
        ).fetchone()
        assert deleted is None
        spec = conn.execute(
            "SELECT clause_count FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()
        assert spec["clause_count"] == 2  # 3 → 2


# ═══════════════════════════════════════════
# 分类编辑
# ═══════════════════════════════════════════

def test_edit_spec_class_form(auth_client, monkeypatch, tmp_path):
    """规范分类编辑表单"""
    db_path = tmp_path / "test_spec_class_edit.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)

    resp = auth_client.get(f"/specs/{spec_id}/edit-class")
    assert resp.status_code == 200
    assert "编辑规范分类" in resp.text


def test_update_spec_class(auth_client, monkeypatch, tmp_path):
    """更新规范分类 dim2/dim3"""
    db_path = tmp_path / "test_update_spec_class.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)

    resp = auth_client.put(
        f"/specs/{spec_id}/class",
        data={"dim2_stage": "施工", "dim3_usage": "民用建筑"},
    )
    assert resp.status_code == 200
    assert "分类已保存" in resp.text

    with get_db() as conn:
        spec = conn.execute(
            "SELECT * FROM specifications WHERE id = ?", (spec_id,)
        ).fetchone()
    assert spec["dim2_stage"] == "施工"
    assert spec["dim3_usage"] == "民用建筑"


def test_update_clause_class(auth_client, monkeypatch, tmp_path):
    """更新条文分类字段 dim4/dim5/dim6"""
    db_path = tmp_path / "test_update_clause_class.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.__init__", lambda self: None,
    )
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.index_clause",
        lambda self, a, b, c, d="": None,
    )
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.put(
        f"/specs/{spec_id}/clauses/{clause['id']}",
        data={
            "clause_no": "1.1", "title": "条文1", "content": "第1条内容测试",
            "dim4_specialty": "结构", "dim5_location": "基础", "dim6_material": "混凝土",
        },
    )
    assert resp.status_code == 200

    with get_db() as conn:
        updated = conn.execute(
            "SELECT * FROM clauses WHERE id = ?", (clause["id"],)
        ).fetchone()
    assert updated["dim4_specialty"] == "结构"
    assert updated["dim5_location"] == "基础"
    assert updated["dim6_material"] == "混凝土"
