"""规范管理与条文 CRUD 路由测试"""
import re

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


def test_specs_list_shows_replacement_hint(auth_client, monkeypatch, tmp_path):
    """状态列必须显示「已由谁替代」——数据早已在 context 里，此前模板没用

    背景：条文详情（`clause_detail.html`）有红色废止提示块，但**列表页的状态列只有三选下拉**
    ⇒ 管理员在列表里看不出「这条废止规范的新版是哪个/是否已入库」，必须逐条点进条文详情。
    2026-09-29 补。两个来源取其一：库内替代者（`replace_by_spec_id` 外键 → 新版的 code）
    或导入时留存的 `replaced_by_code`（新版尚未入库）。
    """
    db_path = tmp_path / "test_specs_replace.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        new_id = _setup_spec_data(conn)          # 新版（在库，且自己不该有提示）
        conn.execute("UPDATE specifications SET code = ?, title = ? WHERE id = ?",
                     ("GB 50204-2015", "混凝土结构工程施工质量验收规范", new_id))
        conn.execute(
            """INSERT INTO specifications (code, title, status, replace_by_spec_id,
                                           replaced_by_code)
               VALUES (?, ?, ?, ?, ?)""",
            ("GB 50204-2002", "混凝土结构工程施工质量验收规范（旧版）",
             "废止", new_id, "GB 50204-2015"),
        )

    html = auth_client.get("/specs/list").text
    hints = re.findall(r'<small class="spec-replace-hint"[^>]*>(.*?)</small>', html, re.S)
    assert len(hints) == 1, f"替代提示应只出现在被替代的那条规范上，实测 {len(hints)} 处"
    assert "GB 50204-2015" in hints[0], f"提示里没有新版编号：{hints[0]!r}"


def test_specs_list_replacement_hint_absent_without_relation(auth_client, monkeypatch, tmp_path):
    """没有替代关系时不得出现提示（否则就是无差别噪音，等同告警疲劳）"""
    db_path = tmp_path / "test_specs_noreplace.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        _setup_spec_data(conn)

    html = auth_client.get("/specs/list").text
    assert "spec-replace-hint" not in html, "无关规范上出现了替代提示"


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


def test_delete_spec_nulls_referencing_replace_by(auth_client, monkeypatch, tmp_path):
    """删除被引用为替代者的新规范 → 200 且旧规范 replace_by_spec_id 置空

    replace_by_spec_id 无 ON DELETE 动作（foreign_keys=ON），删除被 old 引用为
    替代者的新规范时，若不先置空引用会抛 FOREIGN KEY constraint failed → 500。
    """
    db_path = tmp_path / "test_delete_replace_by.db"
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
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
            ("GB 50010-2011", "旧规范", "废止"),
        )
        old_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
            ("GB 50010-2015", "新规范", "现行"),
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 旧规范 replace_by_spec_id 指向新规范（被替代关系）
        conn.execute(
            "UPDATE specifications SET replace_by_spec_id = ? WHERE id = ?",
            (new_id, old_id),
        )

    resp = auth_client.delete(f"/specs/{new_id}")
    assert resp.status_code == 200

    with get_db() as conn:
        assert conn.execute(
            "SELECT id FROM specifications WHERE id = ?", (new_id,)
        ).fetchone() is None, "新规范应被删除"
        old = conn.execute(
            "SELECT replace_by_spec_id FROM specifications WHERE id = ?", (old_id,)
        ).fetchone()
        assert old["replace_by_spec_id"] is None, "旧规范 replace_by_spec_id 应被置空防悬挂"


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

def test_update_clause(auth_client, monkeypatch, tmp_path):
    """更新条文内容"""
    db_path = tmp_path / "test_update_clause.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.__init__", lambda self: None,
    )
    monkeypatch.setattr(
        "app.search.vector_search.VectorStore.index_clause_chunks",
        lambda self, a, b, texts, d="": None,
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


def test_update_clause_reindexes_long_clause_as_chunks(auth_client, monkeypatch, tmp_path):
    """编辑超长条文必须走切块重索引：写回 N 行（chunk_index 递增），尾部文本可召回。

    批二 U5 已把导入/重建/补齐统一到 build_embed_chunks，唯独编辑路径仍是单行
    build_embed_text + index_clause——编辑长条文会把 N 块塌回 1 行、尾部再次不可召回，
    只能等下一次全量重建才恢复。本用例锁死编辑路径也走切块。

    ⚠ 同时锁**面包屑**：编辑路径离开「实参级」守卫（`PRODUCTION_EMBED_CALLERS`）后，
    它只被裸子串检查（`"build_embed_chunks" in src`）覆盖，漏传 `section_path=` 会
    **静默**——这里直接断言写出的向量文本含 fixture 的 section_path（R28）。
    """
    db_path = tmp_path / "test_edit_chunk.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    # 假嵌入：避免加载真实模型（8 维即可满足建表）
    monkeypatch.setattr(
        "app.search.vector_search.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title) VALUES ('GB 50010', '混凝土规范')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, section_path)
               VALUES (?, ?, ?, ?, ?)""",
            (spec_id, "5.1.1", "模板", "短正文", "5 混凝土分项工程 > 5.1 模板"),
        )
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    long_content = "。".join(f"第{i}句内容" for i in range(300)) + "。尾部独有标记"
    resp = auth_client.put(
        f"/specs/{spec_id}/clauses/{clause_id}",
        data={"clause_no": "5.1.1", "title": "模板", "content": long_content},
    )
    assert resp.status_code == 200, resp.text

    from app.search.vector_search import VectorStore
    rows = VectorStore()._get_table().to_arrow().to_pylist()
    mine = [r for r in rows if r["clause_id"] == clause_id]
    assert len(mine) > 1, f"编辑长条文后应写多行子块，实际 {len(mine)} 行（仍走单行 index_clause）"
    assert [r["chunk_index"] for r in sorted(mine, key=lambda r: r["chunk_index"])] == \
        list(range(len(mine))), "chunk_index 应从 0 连续递增"
    assert any("尾部独有标记" in r["text"] for r in mine), \
        "尾部文本不可召回（被塌回单行后被模型截断）"
    # 面包屑必须由编辑路径传给切块入口（section_path 从库内读回，不由本表单提供）。
    # 漏传时向量文本只是少了一段前缀，块数/尾部召回都照常 ⇒ 没有本条断言就是静默的。
    assert all("5 混凝土分项工程 > 5.1 模板" in r["text"] for r in mine), \
        f"编辑路径未把面包屑传进向量文本：{[r['text'][:60] for r in mine]}"


def test_update_clause_to_empty_content_removes_vectors(auth_client, monkeypatch, tmp_path):
    """正文被清空时，该条文已索引的向量必须删掉（不留旧块）。

    空正文不再写「只有前缀」的行（`build_embed_chunks` 返回 []），但仍须删旧向量：
    残留的旧块会被当成当前（已空）条文的命中。该分支由 U7 新引入，此前无任何覆盖
    （R29）。
    """
    db_path = tmp_path / "test_edit_empty.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    # 假嵌入：避免加载真实模型（8 维即可满足建表）
    monkeypatch.setattr(
        "app.search.vector_search.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title) VALUES ('GB 50010', '混凝土规范')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, section_path)
               VALUES (?, ?, ?, ?, ?)""",
            (spec_id, "5.2.1", "原材料", "钢筋进场时应抽取试件作检验。",
             "5 混凝土分项工程 > 5.2 钢筋"),
        )
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 前置事实：先做一次正常编辑，让该条文确有向量行（否则「删干净」是恒真的）
    resp = auth_client.put(
        f"/specs/{spec_id}/clauses/{clause_id}",
        data={"clause_no": "5.2.1", "title": "原材料", "content": "钢筋进场时应抽取试件作检验。"},
    )
    assert resp.status_code == 200, resp.text
    from app.search.vector_search import VectorStore
    before = [r for r in VectorStore()._get_table().to_arrow().to_pylist()
              if r["clause_id"] == clause_id]
    assert before, "前置条件不成立：编辑后该条文应已有向量行"

    # 清空正文
    resp = auth_client.put(
        f"/specs/{spec_id}/clauses/{clause_id}",
        data={"clause_no": "5.2.1", "title": "原材料", "content": ""},
    )
    assert resp.status_code == 200, resp.text

    left = [r for r in VectorStore()._get_table().to_arrow().to_pylist()
            if r["clause_id"] == clause_id]
    assert left == [], f"正文清空后旧向量未删除，残留 {len(left)} 行：{[r['text'][:40] for r in left]}"


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
        "app.search.vector_search.VectorStore.index_clause_chunks",
        lambda self, a, b, texts, d="": None,
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
