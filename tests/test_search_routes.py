"""搜索路由 HTTP 集成测试"""
import pytest
from tests.conftest import setup_search_data  # noqa: F401 — 供各测试通过模块引用


# (setup_search_data 已移至 conftest.py，通过 from tests.conftest import ... 使用)


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


def test_search_all_returns_all_results(auth_client, monkeypatch, tmp_path):
    """all=1 时无参数返回全部条文（用户主动取消所有筛选后的显式全量）"""
    db_path = tmp_path / "test_search_all2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    resp = auth_client.get("/search?all=1")
    assert resp.status_code == 200
    assert "共找到 3 条" in resp.text, "应返回全部 3 条"
    assert "模板设计" in resp.text
    assert "钢筋原材料" in resp.text
    assert "屋面防水" in resp.text
    assert "请输入关键词" not in resp.text, "不应显示空搜索提示"


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
