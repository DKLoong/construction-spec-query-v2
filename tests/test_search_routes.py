"""搜索路由 HTTP 集成测试"""
import pytest


def setup_search_data(conn):
    """写入测试数据"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '混凝土规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    clauses_data = [
        ("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。",
         "结构专业", "主体结构", "模板工程"),
        ("5.2.1", "钢筋原材料", "钢筋进场时应抽取试件作屈服强度检验。",
         "结构专业", "主体结构", "金属材料,钢筋"),
        ("6.1.1", "屋面防水", "屋面防水层应采用卷材或涂膜防水。",
         "建筑专业", "屋面", "防水材料"),
    ]
    for no, title, content, dim4, dim5, dim6 in clauses_data:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content,
               dim4_specialty, dim5_location, dim6_material)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (spec_id, no, title, content, dim4, dim5, dim6),
        )


# ── /search 端点测试 ──

def test_search_keyword_returns_html(auth_client, monkeypatch, tmp_path):
    """关键词搜索返回 HTML"""
    db_path = tmp_path / "test_search_kw.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=钢筋")
    assert resp.status_code == 200
    assert "钢筋" in resp.text


def test_search_dimension_filter(auth_client, monkeypatch, tmp_path):
    """维度筛选返回正确结果"""
    db_path = tmp_path / "test_search_dim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?dim5_location=屋面")
    assert resp.status_code == 200
    assert "屋面防水" in resp.text


def test_search_combined(auth_client, monkeypatch, tmp_path):
    """关键词 + 维度组合筛选"""
    db_path = tmp_path / "test_search_comb.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=防水&dim4_specialty=建筑专业")
    assert resp.status_code == 200
    assert "防水" in resp.text


def test_search_pagination(auth_client, monkeypatch, tmp_path):
    """分页参数生效"""
    db_path = tmp_path / "test_search_page.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=模板&page=1&page_size=2")
    assert resp.status_code == 200
    html = resp.text
    # 每页 2 条，应有分页控件（或至少显示结果计数）
    assert "下一页" in html or "共找到" in html


def test_search_no_keyword(auth_client, monkeypatch, tmp_path):
    """无参数时返回提示信息（非全部结果）"""
    db_path = tmp_path / "test_search_all.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search")
    assert resp.status_code == 200
    assert "请输入关键词" in resp.text


def test_search_no_results(auth_client, monkeypatch, tmp_path):
    """无匹配时显示空结果提示"""
    db_path = tmp_path / "test_search_none.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?keyword=abcdefg不存在的")
    assert resp.status_code == 200
    # 应该包含空结果提示
    assert "暂无" in resp.text or "0 条" in resp.text


def test_search_requires_auth(client):
    """未登录不能访问搜索接口"""
    resp = client.get("/search?keyword=test", follow_redirects=False)
    assert resp.status_code == 302


# ── /clause/{id} 端点测试 ──

def test_clause_detail_returns_html(auth_client, monkeypatch, tmp_path):
    """条文详情返回 HTML"""
    db_path = tmp_path / "test_clause.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
        clause_id = conn.execute(
            "SELECT id FROM clauses WHERE clause_no = '5.1.1'"
        ).fetchone()[0]

    resp = auth_client.get(f"/clause/{clause_id}")
    assert resp.status_code == 200
    assert "模板设计" in resp.text
    assert "GB 50204" in resp.text


def test_clause_detail_not_found(auth_client, monkeypatch, tmp_path):
    """不存在的条文返回 404"""
    db_path = tmp_path / "test_clause_nf.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.get("/clause/99999")
    assert resp.status_code == 404


def test_clause_detail_requires_auth(client):
    """未登录不能访问条文详情"""
    resp = client.get("/clause/1", follow_redirects=False)
    assert resp.status_code == 302
