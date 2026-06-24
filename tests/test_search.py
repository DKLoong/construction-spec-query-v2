from app.search.sql_search import search_clauses
from app.database import init_db, get_db
from app.models import SearchQuery


def setup_search_data(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50204', '混凝土规范')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    clauses_data = [
        ("5.1.1", "模板设计", "模板及其支架应根据工程结构形式进行设计。", "结构专业", "主体结构", "模板工程"),
        ("5.2.1", "钢筋原材料", "钢筋进场时应抽取试件作屈服强度检验。", "结构专业", "主体结构", "金属材料,钢筋"),
        ("6.1.1", "屋面防水", "屋面防水层应采用卷材或涂膜防水。", "建筑专业", "屋面", "防水材料"),
    ]
    for no, title, content, dim4, dim5, dim6 in clauses_data:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, dim4_specialty, dim5_location, dim6_material)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (spec_id, no, title, content, dim4, dim5, dim6),
        )


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
