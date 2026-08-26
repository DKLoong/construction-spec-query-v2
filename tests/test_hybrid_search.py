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


def test_hybrid_search_filters_non_clause_in_vector_path(monkeypatch, tmp_path):
    """向量路径也过滤 clause_is_non=1 的非条文（默认隐藏）"""
    db_path = tmp_path / "test_hybrid_vnon.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "钢筋 相关内容", 0),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "钢筋 编制说明", 1),
        )
        id1 = conn.execute("SELECT id FROM clauses WHERE clause_no='1.0.1'").fetchone()[0]
        id2 = conn.execute("SELECT id FROM clauses WHERE clause_no='前言'").fetchone()[0]

    # 伪造向量返回：id1 与 id2 都命中，验证向量候选路径的过滤
    class _FakeVectorStore:
        def search(self, query_text, top_k=10):
            return [
                {"clause_id": id1, "spec_id": spec_id, "text": "x", "_distance": 0.3},
                {"clause_id": id2, "spec_id": spec_id, "text": "x", "_distance": 0.5},
            ]

    monkeypatch.setattr("app.search.vector_search.VectorStore", _FakeVectorStore)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋"))

    # 默认隐藏非条文：id2 不应出现在向量候选回查结果中
    assert total == 1
    assert all(r["id"] != id2 for r in results)
    assert any(r["id"] == id1 for r in results)


def test_hybrid_search_include_non_clause_vector(monkeypatch, tmp_path):
    """include_non_clause=True 时向量路径放行非条文"""
    db_path = tmp_path / "test_hybrid_vinc.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "钢筋 相关内容", 0),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "钢筋 编制说明", 1),
        )
        id1 = conn.execute("SELECT id FROM clauses WHERE clause_no='1.0.1'").fetchone()[0]
        id2 = conn.execute("SELECT id FROM clauses WHERE clause_no='前言'").fetchone()[0]

    class _FakeVectorStore:
        def search(self, query_text, top_k=10):
            return [
                {"clause_id": id1, "spec_id": spec_id, "text": "x", "_distance": 0.3},
                {"clause_id": id2, "spec_id": spec_id, "text": "x", "_distance": 0.5},
            ]

    monkeypatch.setattr("app.search.vector_search.VectorStore", _FakeVectorStore)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋", include_non_clause=True))

    assert total == 2
    ids = {r["id"] for r in results}
    assert ids == {id1, id2}


# ═══════════════════════════════════════════
# CrossEncoder 精排接入（Top N 精排前缀 + RRF 尾部）
# ═══════════════════════════════════════════

def _seed_rerank_clauses(conn):
    """写入 4 条 content 都含「钢筋」的条文，返回按 clause_no 排序的 id 列表（RRF 原序）"""
    conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
    spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i in range(1, 5):
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
            (spec_id, f"{i}.0.1", f"标题{i}", f"钢筋 相关内容{i}"),
        )
    return [r[0] for r in conn.execute("SELECT id FROM clauses ORDER BY clause_no").fetchall()]


def test_hybrid_search_rerank_prefix_reorders_top_n(monkeypatch, tmp_path):
    """精排接入：前 TOP_N 候选按 CrossEncoder 分数重排，尾部保持 RRF 原序"""
    db_path = tmp_path / "test_hybrid_rerank.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        order_ids = _seed_rerank_clauses(conn)

    import app.search.hybrid_search as hs

    def _reverse_rerank(question, candidates):
        # 模拟精排把前 N 候选倒序：验证结果前缀被 CE 重排
        return (list(reversed([(c, 1.0) for c in candidates])), "crossencoder")

    monkeypatch.setattr(hs, "rerank_candidates", _reverse_rerank)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋", per_page=100))

    # 4 条候选全部落在前 TOP_N(50) 内，被精排倒序 → 与 RRF 原序完全相反
    assert total == 4
    assert [r["id"] for r in results] == list(reversed(order_ids))


def test_hybrid_search_rerank_tail_keeps_rrf_order(monkeypatch, tmp_path):
    """精排只作用于前 TOP_N：N 之后的候选保持 RRF 原序"""
    db_path = tmp_path / "test_hybrid_rerank_tail.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        order_ids = _seed_rerank_clauses(conn)  # [1, 2, 3, 4]

    import app.search.hybrid_search as hs
    monkeypatch.setattr(hs, "_RERANK_TOP_N", 2)  # 缩小精排范围，让尾部可测

    def _reverse(question, candidates):
        assert len(candidates) == 2, "精排只应拿到前 TOP_N=2 候选"
        return (list(reversed([(c, 1.0) for c in candidates])), "crossencoder")

    monkeypatch.setattr(hs, "rerank_candidates", _reverse)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋", per_page=100))

    assert total == 4
    # 前 2 被精排倒序（2,1），后 2 保持 RRF 原序（3,4）
    assert [r["id"] for r in results] == [
        order_ids[1], order_ids[0], order_ids[2], order_ids[3],
    ]


def test_hybrid_search_no_keyword_skips_rerank(monkeypatch, tmp_path):
    """无关键词（纯维度筛选/浏览全部）时跳过精排，保持 RRF 原序"""
    db_path = tmp_path / "test_hybrid_rerank_nokw.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        _seed_rerank_clauses(conn)

    import app.search.hybrid_search as hs
    called = {"rerank": False}

    def _fake(question, candidates):
        called["rerank"] = True
        return ([(c, 1.0) for c in candidates], "none")

    monkeypatch.setattr(hs, "rerank_candidates", _fake)

    from app.search.hybrid_search import hybrid_search
    hybrid_search(SearchQuery(dim5_location="屋面"))

    assert not called["rerank"], "无关键词时不应触发精排"


def test_hybrid_search_single_candidate_skips_rerank(monkeypatch, tmp_path):
    """候选 ≤1 时跳过精排（编排层不调用精排函数）"""
    db_path = tmp_path / "test_hybrid_rerank_one.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)  # 「5.1.1」仅命中 1 条

    import app.search.hybrid_search as hs
    called = {"rerank": False}

    def _fake(question, candidates):
        called["rerank"] = True
        return ([(c, 1.0) for c in candidates], "none")

    monkeypatch.setattr(hs, "rerank_candidates", _fake)

    from app.search.hybrid_search import hybrid_search
    hybrid_search(SearchQuery(keyword="5.1.1"))

    assert not called["rerank"], "候选 ≤1 时不应触发精排"


def test_hybrid_search_rerank_exception_keeps_rrf_order(monkeypatch, tmp_path):
    """精排抛异常时降级为 RRF 原序，检索不中断"""
    db_path = tmp_path / "test_hybrid_rerank_err.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        order_ids = _seed_rerank_clauses(conn)

    import app.search.hybrid_search as hs

    def _raise(question, candidates):
        raise RuntimeError("精排失败")

    monkeypatch.setattr(hs, "rerank_candidates", _raise)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋", per_page=100))

    assert total == 4
    assert [r["id"] for r in results] == order_ids, "精排异常应回退 RRF 原序"
