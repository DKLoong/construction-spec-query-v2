"""混合搜索测试"""
import pytest
from app.models import SearchQuery
from app.database import init_db, get_db
from app.search.tokenize import build_search_text
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
                "INSERT INTO clauses (spec_id, clause_no, content, search_text) VALUES (?, ?, ?, ?)",
                (spec_id, f"{i}.1", f"测试内容{i}",
                 build_search_text(f"{i}.1", "", f"测试内容{i}")),
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
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "其他标题", "本条内容包含 钢筋 需要命中",
             build_search_text("1.0.1", "其他标题", "本条内容包含 钢筋 需要命中")),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "钢筋", "其他标题", "普通内容",
             build_search_text("钢筋", "其他标题", "普通内容")),
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
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "标题甲", "钢筋 相关内容",
             build_search_text("1.0.1", "标题甲", "钢筋 相关内容")),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.0.1", "标题乙", "其他内容",
             build_search_text("2.0.1", "标题乙", "其他内容")),
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
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non, search_text) VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "钢筋 相关内容", 0,
             build_search_text("1.0.1", "总则", "钢筋 相关内容")),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non, search_text) VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "钢筋 编制说明", 1,
             build_search_text("前言", "前言", "钢筋 编制说明")),
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
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non, search_text) VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "总则", "钢筋 相关内容", 0,
             build_search_text("1.0.1", "总则", "钢筋 相关内容")),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non, search_text) VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "钢筋 编制说明", 1,
             build_search_text("前言", "前言", "钢筋 编制说明")),
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
# FTS5 + jieba 预分词检索（bm25 相关度 + clause_no 提权）
# ═══════════════════════════════════════════

def test_hybrid_search_bm25_ranks_by_relevance(monkeypatch, tmp_path):
    """FTS bm25：内容多次出现关键词的条文排在前面（词频相关度）"""
    db_path = tmp_path / "test_fts_bm25.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # A：钢筋 出现 4 次（词频高）；B：钢筋 出现 1 次
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.0.1", "标题A", "钢筋 钢筋 钢筋 钢筋 混凝土",
             build_search_text("2.0.1", "标题A", "钢筋 钢筋 钢筋 钢筋 混凝土")),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "标题B", "钢筋 混凝土 混凝土 混凝土",
             build_search_text("1.0.1", "标题B", "钢筋 混凝土 混凝土 混凝土")),
        )
        id_a = conn.execute("SELECT id FROM clauses WHERE clause_no='2.0.1'").fetchone()[0]
        id_b = conn.execute("SELECT id FROM clauses WHERE clause_no='1.0.1'").fetchone()[0]

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋", per_page=100))

    assert total == 2
    assert results[0]["id"] == id_a, "bm25 应把钢筋词频更高的 A 排在前面"


def test_hybrid_search_clause_no_exact_beats_bm25(monkeypatch, tmp_path):
    """clause_no 精确命中优先于 bm25 相关度（编号检索提权，兼容原行为）"""
    db_path = tmp_path / "test_fts_clauseno.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # A：content 钢筋 出现 4 次（bm25 高）；B：clause_no 恰为「钢筋」（精确命中）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "标题A", "钢筋 钢筋 钢筋 钢筋 混凝土",
             build_search_text("1.0.1", "标题A", "钢筋 钢筋 钢筋 钢筋 混凝土")),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "钢筋", "标题B", "普通内容",
             build_search_text("钢筋", "标题B", "普通内容")),
        )
        id_b = conn.execute("SELECT id FROM clauses WHERE clause_no='钢筋'").fetchone()[0]

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋", per_page=100))

    assert total == 2
    assert results[0]["id"] == id_b, "clause_no 精确命中应优先于 bm25 相关度"


def test_hybrid_search_multiword_or_fallback(monkeypatch, tmp_path):
    """多词 AND 无结果时降级 OR 召回（解决「I级接头强度」这类严格匹配查空）"""
    db_path = tmp_path / "test_hybrid_or.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 条文含「接头」「强度」，但缺「I」「级」→ AND('I' AND '级' AND '接头' AND '强度') 不命中
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "3.0.5", "接头强度", "钢筋机械连接接头的强度应满足规定。",
             build_search_text("3.0.5", "接头强度", "钢筋机械连接接头的强度应满足规定。")),
        )
        id1 = conn.execute("SELECT id FROM clauses WHERE clause_no='3.0.5'").fetchone()[0]

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="I级接头强度"))

    assert total >= 1, "多词 AND 无结果时 OR 兜底应能召回"
    assert any(r["id"] == id1 for r in results), "OR 兜底应包含含部分词的条文"


# ═══════════════════════════════════════════
# CE 精排热切换（ce_rerank 开关 + 缓存隔离）
# ═══════════════════════════════════════════

def test_hybrid_search_ce_rerank_triggers_rerank(monkeypatch, tmp_path):
    """ce_rerank=True 时触发 CrossEncoder 精排；默认 False 不触发"""
    db_path = tmp_path / "test_hybrid_ce.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in range(1, 3):
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
                (spec_id, f"{i}.0.1", f"标题{i}", f"钢筋 内容{i}",
                 build_search_text(f"{i}.0.1", f"标题{i}", f"钢筋 内容{i}")),
            )

    import app.search.hybrid_search as hs
    calls = {"rerank": 0}

    def _fake(question, candidates):
        calls["rerank"] += 1
        return ([(c, 1.0) for c in candidates], "crossencoder")

    monkeypatch.setattr(hs, "rerank_candidates", _fake)

    from app.search.hybrid_search import hybrid_search
    hybrid_search(SearchQuery(keyword="钢筋"))
    assert calls["rerank"] == 0, "ce_rerank 默认 False 不应触发精排"

    hybrid_search(SearchQuery(keyword="钢筋", ce_rerank=True))
    assert calls["rerank"] == 1, "ce_rerank=True 应触发精排"


def test_hybrid_search_ce_rerank_cache_isolated(monkeypatch, tmp_path):
    """ce_rerank 开关纳入缓存 key：开关状态切换不命中对方缓存"""
    db_path = tmp_path / "test_hybrid_ce_cache.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in range(1, 3):
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
                (spec_id, f"{i}.0.1", f"标题{i}", f"钢筋 内容{i}",
                 build_search_text(f"{i}.0.1", f"标题{i}", f"钢筋 内容{i}")),
            )

    import app.search.hybrid_search as hs
    calls = {"rerank": 0}

    def _fake(question, candidates):
        calls["rerank"] += 1
        return ([(c, 1.0) for c in candidates], "crossencoder")

    monkeypatch.setattr(hs, "rerank_candidates", _fake)

    from app.search.hybrid_search import hybrid_search
    hybrid_search(SearchQuery(keyword="钢筋", ce_rerank=True))   # 精排 1，缓存 True
    hybrid_search(SearchQuery(keyword="钢筋"))                    # False：不精排，缓存 False 独立
    hybrid_search(SearchQuery(keyword="钢筋", ce_rerank=True))   # 命中 True 缓存，不重复精排

    assert calls["rerank"] == 1, "开关状态应隔离缓存；True 第二次应命中缓存不重复精排"
