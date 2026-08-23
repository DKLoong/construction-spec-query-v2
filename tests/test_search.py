from app.search.sql_search import search_clauses
from app.database import init_db, get_db
from app.models import SearchQuery
from tests.conftest import setup_search_data


def test_search_keyword(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(keyword="钢筋"))
    assert total >= 1
    assert any("钢筋" in r["content"] for r in results)


def test_search_dimension_filter(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(dim5_location="屋面"))
    assert total >= 1
    assert results[0]["dim5_location"] == "屋面"


def test_search_combined(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(keyword="混凝土", dim6_material="模板"))
    assert total == 0


def test_search_pagination(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(page=1, per_page=2))
    assert len(results) <= 2
    assert total == 3


def test_search_clause_no_exact_priority(monkeypatch, tmp_path):
    """字段优先级排序：clause_no 精确命中 > title 命中 > content 命中"""
    db_path = tmp_path / "test_priority.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 仅 content 命中（最低优先级）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "1.0.1", "其他标题", "本条内容包含关键词 钢筋 需要命中"),
        )
        # 仅 title 命中（中优先级）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "2.0.1", "钢筋 验收", "普通内容"),
        )
        # clause_no 精确命中（最高优先级）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "钢筋", "其他标题", "普通内容"),
        )

    results, total = search_clauses(SearchQuery(keyword="钢筋"))

    assert total == 3
    clause_nos = [r["clause_no"] for r in results]
    # 编号精确命中排最前，title 命中排在 content 命中之前
    assert clause_nos[0] == "钢筋"
    assert clause_nos.index("2.0.1") < clause_nos.index("1.0.1")


def test_vector_store_import():
    from app.search.vector_search import VectorStore
    assert hasattr(VectorStore, "search")
    assert hasattr(VectorStore, "index_clause")
