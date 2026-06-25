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


def test_vector_store_import():
    from app.search.vector_search import VectorStore
    assert hasattr(VectorStore, "search")
    assert hasattr(VectorStore, "index_clause")
