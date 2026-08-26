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


def test_delete_spec_returns_oob_clear_for_detail_area(auth_client, monkeypatch, tmp_path):
    """删除规范后须 OOB 清空条文详情区与分类编辑区

    查看条文后删除整本规范，若仅删除列表行，详情区仍滞留被删规范的条文。
    响应应含 hx-swap-oob 的占位 div，把 #clause-detail-area / #spec-class-area 清空。
    """
    db_path = tmp_path / "test_delete_oob.db"
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

    resp = auth_client.delete(f"/specs/{spec_id}")
    assert resp.status_code == 200
    assert 'id="clause-detail-area"' in resp.text, "删除响应应包含 clause-detail-area OOB 元素"
    assert 'id="spec-class-area"' in resp.text, "删除响应应包含 spec-class-area OOB 元素"
    assert 'hx-swap-oob="true"' in resp.text, "OOB 元素须带 hx-swap-oob=true"


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
    # content 经 tojson 编码到 data-md（避免明文 XSS 面），预览列渲染容器存在
    assert "clause-preview-md" in resp.text
    assert "data-md=" in resp.text


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
        # 编辑后 search_text 同步更新（jieba 分词），FTS 表可检索到新内容
        assert updated["search_text"] and "更新" in updated["search_text"], \
            "编辑后 search_text 应含新内容的分词"
        # 与 sql_search 一致：用 build_match_query 构造 MATCH（裸词不命中中文分词）
        from app.search.tokenize import build_match_query
        fts = conn.execute(
            "SELECT rowid FROM clauses_fts WHERE clauses_fts MATCH ?",
            (build_match_query("更新后"),),
        ).fetchall()
        assert any(r["rowid"] == clause["id"] for r in fts), \
            "FTS 应能检索到更新后的条文"
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


def test_edit_clause_page(auth_client, monkeypatch, tmp_path):
    """条文编辑页返回两栏布局（左编辑右预览）"""
    db_path = tmp_path / "test_edit_page.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.get(f"/specs/{spec_id}/clauses/{clause['id']}/edit-page")
    assert resp.status_code == 200
    assert "review-panels" in resp.text, "编辑页应为双栏布局"
    assert "clause_edit_page" in resp.text or "编辑条文" in resp.text


def test_clause_class_row(auth_client, monkeypatch, tmp_path):
    """条文分类普通行路由：供分类编辑取消时恢复单行，避免整列表重渲染跳顶"""
    db_path = tmp_path / "test_class_row.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.get(f"/specs/{spec_id}/clauses/{clause['id']}/row-class")
    assert resp.status_code == 200
    assert f'id="clause-row-{clause["id"]}"' in resp.text, "应返回当前条文的普通行"
    # 普通行含操作按钮（编辑/分类/删除），而非编辑表单的输入框
    assert "分类" in resp.text, "普通行应含分类操作按钮"
    assert 'name="dim4_specialty"' not in resp.text, "不应返回分类编辑表单"


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


def test_edit_clause_class_form(auth_client, monkeypatch, tmp_path):
    """条文分类编辑表单返回 HTML"""
    db_path = tmp_path / "test_edit_clause_class.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.get(f"/specs/{spec_id}/clauses/{clause['id']}/edit-class")
    assert resp.status_code == 200
    assert "专业" in resp.text


def test_update_clause_class_only(auth_client, monkeypatch, tmp_path):
    """仅更新条文分类（不修改内容）"""
    db_path = tmp_path / "test_update_clause_class_only.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause = conn.execute(
            "SELECT * FROM clauses WHERE spec_id = ? LIMIT 1", (spec_id,)
        ).fetchone()

    resp = auth_client.put(
        f"/specs/{spec_id}/clauses/{clause['id']}/class",
        data={"dim4_specialty": "结构", "dim5_location": "基础", "dim6_material": "混凝土"},
    )
    assert resp.status_code == 200
    assert "结构" in resp.text
    assert "基础" in resp.text
    assert "混凝土" in resp.text

    with get_db() as conn:
        updated = conn.execute(
            "SELECT * FROM clauses WHERE id = ?", (clause["id"],)
        ).fetchone()
    assert updated["dim4_specialty"] == "结构"
    assert updated["dim5_location"] == "基础"
    assert updated["dim6_material"] == "混凝土"
    # 确认内容未被修改
    assert updated["clause_no"] == clause["clause_no"]
    assert updated["title"] == clause["title"]


# ═══════════════════════════════════════════
# 条文图片服务
# ═══════════════════════════════════════════

def _setup_spec_with_imgs(db_path, spec_out):
    """建库 + 插带 output_dir 的 spec，返回 spec_id"""
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, output_dir) VALUES (?, ?, ?)",
            ("GB-TEST-IMG", "图片规范", str(spec_out)),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_spec_image_serves_file(auth_client, monkeypatch, tmp_path):
    """按 spec.output_dir/imgs 返回图片文件"""
    db_path = tmp_path / "test_spec_img.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))

    spec_out = tmp_path / "spec_out"
    imgs = spec_out / "imgs"
    imgs.mkdir(parents=True)
    (imgs / "a.jpg").write_bytes(b"img-data")

    spec_id = _setup_spec_with_imgs(db_path, spec_out)
    resp = auth_client.get(f"/specs/{spec_id}/imgs/a.jpg")
    assert resp.status_code == 200
    assert resp.content == b"img-data"


def test_spec_image_404_when_missing(auth_client, monkeypatch, tmp_path):
    """图片文件缺失 → 404"""
    db_path = tmp_path / "test_spec_img2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))

    spec_out = tmp_path / "spec_out2"
    spec_out.mkdir()

    spec_id = _setup_spec_with_imgs(db_path, spec_out)
    resp = auth_client.get(f"/specs/{spec_id}/imgs/nope.jpg")
    assert resp.status_code == 404


def test_spec_image_404_when_no_output_dir(auth_client, monkeypatch, tmp_path):
    """spec 无 output_dir → 404"""
    db_path = tmp_path / "test_spec_img3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    spec_id = _setup_spec_with_imgs(db_path, tmp_path)  # 复用：先插带目录的，再手动置空
    from app.database import get_db
    with get_db() as conn:
        conn.execute("UPDATE specifications SET output_dir = NULL WHERE id = ?", (spec_id,))

    resp = auth_client.get(f"/specs/{spec_id}/imgs/a.jpg")
    assert resp.status_code == 404


def test_spec_image_rejects_path_traversal(auth_client, monkeypatch, tmp_path):
    """filename 含 ..%2F → 404，不越权读取"""
    db_path = tmp_path / "test_spec_img4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))

    spec_out = tmp_path / "spec_out4"
    spec_out.mkdir()
    (spec_out / "secret.txt").write_bytes(b"top-secret")

    spec_id = _setup_spec_with_imgs(db_path, spec_out)
    resp = auth_client.get(f"/specs/{spec_id}/imgs/..%2Fsecret.txt")
    assert resp.status_code == 404
    assert b"top-secret" not in resp.content
