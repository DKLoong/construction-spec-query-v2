"""同义词管理路由测试"""
import pytest


def test_synonyms_page_returns_200(auth_client):
    """同义词管理页需要鉴权"""
    resp = auth_client.get("/synonyms")
    assert resp.status_code == 200


def test_synonyms_requires_auth(client):
    """未登录不能访问同义词管理页"""
    resp = client.get("/synonyms", follow_redirects=False)
    assert resp.status_code == 302


def test_synonyms_list_returns_html(auth_client, monkeypatch, tmp_path):
    """同义词列表返回 HTML 片段"""
    db_path = tmp_path / "test_syn_list.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM synonym_map")  # 清空预置种子，构造独立测试数据
        conn.execute(
            "INSERT INTO synonym_map (source, target) VALUES (?, ?)", ("砼", "混凝土")
        )

    resp = auth_client.get("/synonyms/list")
    assert resp.status_code == 200
    assert "砼" in resp.text
    assert "混凝土" in resp.text


def test_synonyms_list_filter_active(auth_client, monkeypatch, tmp_path):
    """支持按 active 过滤：禁用项不显示"""
    db_path = tmp_path / "test_syn_filter.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM synonym_map")  # 清空预置种子
        conn.execute(
            "INSERT INTO synonym_map (source, target, is_active) VALUES (?, ?, ?)",
            ("砼", "混凝土", 1),
        )
        conn.execute(
            "INSERT INTO synonym_map (source, target, is_active) VALUES (?, ?, ?)",
            ("箍筋", "钢筋", 0),
        )

    resp = auth_client.get("/synonyms/list?active=1")
    assert resp.status_code == 200
    assert "砼" in resp.text
    assert "箍筋" not in resp.text


def test_create_synonym(auth_client, monkeypatch, tmp_path):
    """创建同义词"""
    db_path = tmp_path / "test_syn_create.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM synonym_map")  # 清空预置种子
    resp = auth_client.post("/synonyms/create", data={"source": "砼", "target": "混凝土"})
    assert resp.status_code == 200
    # 触发前端 #synonyms-table 刷新事件（去掉残留 load div 后的单一数据源机制）
    assert resp.headers.get("HX-Trigger") == "synonymsUpdated"

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM synonym_map WHERE source = ?", ("砼",)
        ).fetchone()
    assert row is not None
    assert row["target"] == "混凝土"
    assert row["is_active"] == 1


def test_create_synonym_duplicate_does_not_500(auth_client, monkeypatch, tmp_path):
    """重复创建相同原词不应报错（INSERT OR IGNORE 兜底）"""
    db_path = tmp_path / "test_syn_dup.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db

    init_db()
    resp = auth_client.post("/synonyms/create", data={"source": "砼", "target": "混凝土"})
    assert resp.status_code == 200
    # 预置种子已含「砼」，再次创建不应 500，也不应产生重复行
    resp = auth_client.post("/synonyms/create", data={"source": "砼", "target": "混凝土"})
    assert resp.status_code == 200


def test_create_synonym_validation_empty(auth_client, monkeypatch, tmp_path):
    """原词为空应返回错误"""
    db_path = tmp_path / "test_syn_empty.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db

    init_db()
    resp = auth_client.post("/synonyms/create", data={"source": "", "target": "混凝土"})
    assert resp.status_code == 400
    assert "原词不能为空" in resp.text


def test_create_synonym_validation_same(auth_client, monkeypatch, tmp_path):
    """原词与目标词相同应返回错误"""
    db_path = tmp_path / "test_syn_same.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db

    init_db()
    resp = auth_client.post("/synonyms/create", data={"source": "混凝土", "target": "混凝土"})
    assert resp.status_code == 400
    assert "不能相同" in resp.text


def test_toggle_synonym(auth_client, monkeypatch, tmp_path):
    """启用/禁用同义词"""
    db_path = tmp_path / "test_syn_toggle.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM synonym_map")  # 清空预置种子
        conn.execute(
            "INSERT INTO synonym_map (source, target) VALUES (?, ?)", ("砼", "混凝土")
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.post(f"/synonyms/{sid}/toggle")
    assert resp.status_code == 200

    with get_db() as conn:
        row = conn.execute("SELECT is_active FROM synonym_map WHERE id = ?", (sid,)).fetchone()
    assert row["is_active"] == 0

    resp = auth_client.post(f"/synonyms/{sid}/toggle")
    assert resp.status_code == 200

    with get_db() as conn:
        row = conn.execute("SELECT is_active FROM synonym_map WHERE id = ?", (sid,)).fetchone()
    assert row["is_active"] == 1


def test_delete_synonym(auth_client, monkeypatch, tmp_path):
    """删除同义词"""
    db_path = tmp_path / "test_syn_del.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db

    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM synonym_map")  # 清空预置种子
        conn.execute(
            "INSERT INTO synonym_map (source, target) VALUES (?, ?)", ("砼", "混凝土")
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = auth_client.delete(f"/synonyms/{sid}")
    assert resp.status_code == 200

    with get_db() as conn:
        row = conn.execute("SELECT * FROM synonym_map WHERE id = ?", (sid,)).fetchone()
    assert row is None
