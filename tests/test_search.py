from app.search.sql_search import search_clauses
from app.database import init_db, get_db
from app.models import SearchQuery
from app.search.tokenize import build_search_text
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
        # 仅 content 命中（content 分词含关键词）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "其他标题", "本条内容包含关键词 钢筋 需要命中",
             build_search_text("1.0.1", "其他标题", "本条内容包含关键词 钢筋 需要命中")),
        )
        # title 命中（title 分词含关键词）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.0.1", "钢筋 验收", "普通内容",
             build_search_text("2.0.1", "钢筋 验收", "普通内容")),
        )
        # clause_no 精确命中（最高优先级）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "钢筋", "其他标题", "普通内容",
             build_search_text("钢筋", "其他标题", "普通内容")),
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


def test_search_filters_non_clause_by_default(monkeypatch, tmp_path):
    """sql_search 默认过滤 clause_is_non=1 的非条文"""
    db_path = tmp_path / "test_search_nonclause.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "正常条文内容", 0),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "本规范编制说明", 1),
        )

    results, total = search_clauses(SearchQuery())
    assert total == 1
    assert all(r["clause_is_non"] == 0 for r in results)
    assert results[0]["clause_no"] == "1.0.1"


def test_search_include_non_clause(monkeypatch, tmp_path):
    """include_non_clause=True 时返回非条文"""
    db_path = tmp_path / "test_search_inc.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "正常条文内容", 0),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "本规范编制说明", 1),
        )

    results, total = search_clauses(SearchQuery(include_non_clause=True))
    assert total == 2
    clause_nos = {r["clause_no"] for r in results}
    assert clause_nos == {"1.0.1", "前言"}


def test_search_title_priority_over_content(monkeypatch, tmp_path):
    """title 命中应排在 content 命中之前（字段信号，bm25 无法区分标题 vs 正文）"""
    db_path = tmp_path / "test_title_prio.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # A：content 钢筋 3 次（bm25 高）；B：title 含钢筋 1 次（标题命中应优先）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "其他标题", "钢筋 钢筋 钢筋 混凝土",
             build_search_text("1.0.1", "其他标题", "钢筋 钢筋 钢筋 混凝土")),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.0.1", "钢筋 验收", "普通内容",
             build_search_text("2.0.1", "钢筋 验收", "普通内容")),
        )
        id_b = conn.execute("SELECT id FROM clauses WHERE clause_no='2.0.1'").fetchone()[0]

    results, total = search_clauses(SearchQuery(keyword="钢筋"))
    assert total == 2
    assert results[0]["id"] == id_b, "title 命中应排在 content 命中（即使 bm25 词频更高）之前"
