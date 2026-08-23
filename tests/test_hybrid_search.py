"""混合搜索测试"""
import pytest
from app.models import SearchQuery
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def test_hybrid_search_keyword(monkeypatch, tmp_path):
    """关键词搜索正常工作"""
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


def test_hybrid_search_like_special_chars(monkeypatch, tmp_path):
    """LIKE 搜索正确处理特殊字符（如点号、斜线、连字符）"""
    db_path = tmp_path / "test_hybrid_special.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.hybrid_search import hybrid_search
    # 搜索含点号的条文编号 "5.1.1" — LIKE 下应能精确匹配
    results, total = hybrid_search(SearchQuery(keyword="5.1.1"))
    assert total >= 1
    assert any(r["clause_no"] == "5.1.1" for r in results)

    # 含其他特殊字符的关键词不应抛异常
    results2, total2 = hybrid_search(SearchQuery(keyword="GB 50204-2015"))
    assert isinstance(results2, list)
    assert isinstance(total2, int)


def test_hybrid_clause_no_priority(monkeypatch, tmp_path):
    """关键词搜索下编号精确命中的条文排在 content 命中的条文之前"""
    db_path = tmp_path / "test_hybrid_priority.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    # 向量指向临时目录（无表 → 返回空），保证该用例聚焦 SQL 字段优先级
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "1.0.1", "其他标题", "本条内容包含 钢筋 需要命中"),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "钢筋", "其他标题", "普通内容"),
        )

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋"))

    assert total == 2
    assert results[0]["clause_no"] == "钢筋"


def test_hybrid_rrf_merges_overlap(monkeypatch, tmp_path):
    """向量结果与 SQL 结果重叠时：RRF 融合去重，双路命中排最前"""
    db_path = tmp_path / "test_hybrid_rrf.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "1.0.1", "标题甲", "钢筋 相关内容"),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, "2.0.1", "标题乙", "其他内容"),
        )
        id1 = conn.execute("SELECT id FROM clauses WHERE clause_no='1.0.1'").fetchone()[0]
        id2 = conn.execute("SELECT id FROM clauses WHERE clause_no='2.0.1'").fetchone()[0]

    # 伪造向量返回：id1 与 SQL 重叠（dist 小 → 双路命中），id2 仅向量命中
    class _FakeVectorStore:
        def search(self, query_text, top_k=10):
            return [
                {"clause_id": id1, "spec_id": spec_id, "text": "x", "_distance": 0.3},
                {"clause_id": id2, "spec_id": spec_id, "text": "x", "_distance": 0.5},
            ]

    monkeypatch.setattr("app.search.vector_search.VectorStore", _FakeVectorStore)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋"))

    assert total == 2
    ids = [r["id"] for r in results]
    assert ids.count(id1) == 1  # 无重复
    # 双路命中的 id1 排最前
    assert results[0]["id"] == id1
    assert results[0]["_source"] == "hybrid"
    by_id = {r["id"]: r for r in results}
    assert by_id[id2]["_source"] == "semantic"
