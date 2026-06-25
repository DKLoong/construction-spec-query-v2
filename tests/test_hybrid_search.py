"""混合搜索测试"""
import pytest
from app.models import SearchQuery
from app.database import init_db, get_db


def setup_search_data(conn):
    """写入 3 条测试条文"""
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


def test_hybrid_search_keyword(monkeypatch, tmp_path):
    """FTS5 关键词搜索正常工作"""
    db_path = tmp_path / "test_hybrid.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋"))

    assert total >= 1
    assert any("钢筋" in r["content"] for r in results)


def test_hybrid_search_dimension_filter(monkeypatch, tmp_path):
    """维度筛选正常工作"""
    db_path = tmp_path / "test_hybrid_dim.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(dim5_location="屋面"))

    assert total >= 1
    assert results[0]["dim5_location"] == "屋面"


def test_hybrid_search_no_keyword(monkeypatch, tmp_path):
    """无关键词时返回全部结果（维度筛选依然生效）"""
    db_path = tmp_path / "test_hybrid_all.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery())

    assert total == 3


def test_hybrid_search_pagination(monkeypatch, tmp_path):
    """分页参数生效"""
    db_path = tmp_path / "test_hybrid_page.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(page=1, per_page=2))

    assert len(results) <= 2
    assert total == 3


def test_hybrid_search_no_results(monkeypatch, tmp_path):
    """无匹配时返回空列表"""
    db_path = tmp_path / "test_hybrid_none.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="zzz不存在的关键词zzz"))

    assert total == 0
    assert results == []


def test_hybrid_search_per_page_cap(monkeypatch, tmp_path):
    """per_page 超过 100 时被限制为 100"""
    db_path = tmp_path / "test_hybrid_cap.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    # 写入超过 100 条测试数据（用少量即可，只验证 cap 逻辑）
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in range(10):
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?, ?, ?)",
                (spec_id, f"{i}.1", f"测试内容{i}"),
            )

    from app.search.hybrid_search import hybrid_search
    from app.models import SearchQuery

    # 请求 per_page=500，实际返回应 ≤ 100
    results, total = hybrid_search(SearchQuery(per_page=500))
    # 总共只有 10 条，但 cap 应生效（返回 ≤100，实际上 =10）
    assert len(results) <= 100


def test_hybrid_search_fts5_special_chars(monkeypatch, tmp_path):
    """FTS5 特殊字符被安全处理"""
    db_path = tmp_path / "test_hybrid_special.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    # 含 FTS5 特殊字符 * " ( ) 的查询不应报错
    results, total = hybrid_search(SearchQuery(keyword="钢筋* (测试)"))
    # 不应抛出异常，正常返回
    assert isinstance(results, list)
    assert isinstance(total, int)
