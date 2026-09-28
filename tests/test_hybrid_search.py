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
    results, total = hybrid_search(SearchQuery(dim5_location=["屋面"]))

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
                 build_search_text(f"{i}.1", "", f"测试内容{i}")[0]),
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
             build_search_text("1.0.1", "其他标题", "本条内容包含 钢筋 需要命中")[0]),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "钢筋", "其他标题", "普通内容",
             build_search_text("钢筋", "其他标题", "普通内容")[0]),
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
             build_search_text("1.0.1", "标题甲", "钢筋 相关内容")[0]),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.0.1", "标题乙", "其他内容",
             build_search_text("2.0.1", "标题乙", "其他内容")[0]),
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
             build_search_text("1.0.1", "总则", "钢筋 相关内容")[0]),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non, search_text) VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "钢筋 编制说明", 1,
             build_search_text("前言", "前言", "钢筋 编制说明")[0]),
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
             build_search_text("1.0.1", "总则", "钢筋 相关内容")[0]),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, clause_is_non, search_text) VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "前言", "前言", "钢筋 编制说明", 1,
             build_search_text("前言", "前言", "钢筋 编制说明")[0]),
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
             build_search_text("2.0.1", "标题A", "钢筋 钢筋 钢筋 钢筋 混凝土")[0]),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "标题B", "钢筋 混凝土 混凝土 混凝土",
             build_search_text("1.0.1", "标题B", "钢筋 混凝土 混凝土 混凝土")[0]),
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
             build_search_text("1.0.1", "标题A", "钢筋 钢筋 钢筋 钢筋 混凝土")[0]),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "钢筋", "标题B", "普通内容",
             build_search_text("钢筋", "标题B", "普通内容")[0]),
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
             build_search_text("3.0.5", "接头强度", "钢筋机械连接接头的强度应满足规定。")[0]),
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
                 build_search_text(f"{i}.0.1", f"标题{i}", f"钢筋 内容{i}")[0]),
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
                 build_search_text(f"{i}.0.1", f"标题{i}", f"钢筋 内容{i}")[0]),
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


# ═══════════════════════════════════════════
# 向量臂去重（Task 9：超取 ×10 → 按 clause 取最优块 → 截回 top_k）
# ═══════════════════════════════════════════

def test_dist_map_keeps_best_chunk_not_last():
    """同一条文多块时，必须保留**最小**（最近）距离，而非最后写入的那个"""
    dist_map: dict[int, float] = {}
    for cid, d in ((7, 0.9), (7, 0.3), (7, 0.7)):     # 命中顺序任意
        dist_map[cid] = min(dist_map.get(cid, float("inf")), d)
    assert dist_map[7] == 0.3


def test_search_overfetches_before_dedup(monkeypatch):
    """去重发生在检索之后 → 必须先超取，否则有效召回被自己挤掉。

    倍数**唯一定义在 app.search.vector_search**（超取/去重/截断同一处实现，
    放在调用方会让两处倍数静默分叉）；这里只钉住「倍数 ≥ 2」这一前提。
    """
    from app.search import vector_search as vs
    assert vs._VECTOR_FETCH_MULTIPLIER >= 2


def test_dedupe_by_clause_keeps_highest_scoring_row():
    """去重语义：同一条文同一个席位里留下的是 `_distance` 最小的那块。

    ⚠ 显式 `max_per_clause=1`：R9 的默认上限是 2（每个条文可保留 2 块），
    默认值下 clause 7 的两块都该留下，本用例要钉的是「取最优块」而非上限。
    """
    from app.search.vector_search import _dedupe_by_clause
    rows = [
        {"clause_id": 7, "chunk_index": 0, "_distance": 0.9, "text": "a"},
        {"clause_id": 7, "chunk_index": 1, "_distance": 0.3, "text": "b"},
        {"clause_id": 8, "chunk_index": 0, "_distance": 0.5, "text": "c"},
    ]
    out = _dedupe_by_clause(rows, max_per_clause=1)
    assert [r["clause_id"] for r in out] == [7, 8]
    assert out[0]["_distance"] == 0.3 and out[0]["chunk_index"] == 1
    # 默认上限按 R9 是 2：clause 7 的两块都保留，仍按距离升序
    assert [r["clause_id"] for r in _dedupe_by_clause(rows)] == [7, 8, 7]


def test_dedupe_caps_chunks_per_clause_and_truncates_to_top_k():
    """R2/R9：每 clause ≤2 块，且整体按距离截回 top_k。

    没有每-clause 上限时，一条长条文的块可占满全部块位（外部评审的偏斜场景）；
    没有截断时，向量臂的 RRF 席位被静默放大。
    """
    from app.search.vector_search import _dedupe_by_clause
    rows = [{"clause_id": 1, "chunk_index": i, "_distance": 0.1 + i * 0.01, "text": "x"}
            for i in range(6)]                       # 同一条文的 6 个块
    rows += [{"clause_id": c, "chunk_index": 0, "_distance": 0.5, "text": "y"}
             for c in (2, 3, 4)]
    out = _dedupe_by_clause(rows, max_per_clause=2, top_k=3)
    assert len(out) == 3, "必须截回 top_k"
    assert sum(1 for r in out if r["clause_id"] == 1) <= 2, "同一条文最多 2 块"
    assert {r["clause_id"] for r in out} >= {1, 2}, "其他条文必须仍有席位"


def test_vector_store_search_oversamples_then_dedupes_and_truncates(monkeypatch, tmp_path):
    """**R2 固定顺序的行为化断言**：LanceDB 取 top_k×倍数 → 去重取最优 → 返回 ≤ top_k。

    整链一次跑完（不是只查常量）：漏了超取 → 取的行数不对；漏了去重 → 同一条文
    出现两行；漏了截断 → 返回行数超过 top_k。
    """
    from app.search import vector_search as vsmod

    limits: list[int] = []

    class _Query:
        def limit(self, n):
            limits.append(n)
            return self

        def to_list(self):
            return [
                {"clause_id": 7, "spec_id": 1, "text": "块0", "chunk_index": 0, "_distance": 0.9},
                {"clause_id": 7, "spec_id": 1, "text": "块1", "chunk_index": 1, "_distance": 0.2},
                {"clause_id": 8, "spec_id": 1, "text": "块0", "chunk_index": 0, "_distance": 0.4},
            ]

    class _Tbl:
        def search(self, q_vec, vector_column_name=None):  # noqa: ARG002 — 替身签名
            return _Query()

    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    monkeypatch.setattr(vsmod.VectorStore, "_table_exists", lambda self: True)
    monkeypatch.setattr(vsmod.VectorStore, "_get_table", lambda self: _Tbl())
    monkeypatch.setattr(vsmod, "embed_texts", lambda texts: [[0.0] * 4 for _ in texts])

    rows = vsmod.VectorStore().search("钢筋", top_k=2)

    assert limits == [2 * vsmod._VECTOR_FETCH_MULTIPLIER], \
        f"未按 top_k×倍数超取: {limits}"
    assert len(rows) <= 2, "必须截回 top_k"
    assert [r["clause_id"] for r in rows] == [7, 8], "每条文一行、按距离升序"
    assert rows[0]["_distance"] == 0.2 and rows[0]["chunk_index"] == 1, "必须取最优块"
    assert {"clause_id", "spec_id", "text", "chunk_index", "_distance"} <= set(rows[0])


def test_vector_arm_keeps_best_chunk_distance(monkeypatch, tmp_path):
    """端到端：同一条文 3 块（距离 0.9/0.3/0.5）→ 结果里的 _distance 必须是 0.3。

    原实现 `dist_map[id] = dist` 是**后者覆盖**，会留下最差的那块（0.5 或 0.9），
    使该条文的 RRF 名次被自己的烂块拖下去。
    """
    db_path = tmp_path / "test_best_chunk.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "9.9.9", "标题", "其他内容",
             build_search_text("9.9.9", "标题", "其他内容")[0]),
        )
        cid = conn.execute("SELECT id FROM clauses WHERE clause_no='9.9.9'").fetchone()[0]

    class _FakeVectorStore:
        def search(self, query_text, top_k=10):
            return [
                {"clause_id": cid, "spec_id": spec_id, "text": "块0",
                 "chunk_index": 0, "_distance": 0.9},
                {"clause_id": cid, "spec_id": spec_id, "text": "块1",
                 "chunk_index": 1, "_distance": 0.3},
                {"clause_id": cid, "spec_id": spec_id, "text": "块2",
                 "chunk_index": 2, "_distance": 0.5},
            ]

    monkeypatch.setattr("app.search.vector_search.VectorStore", _FakeVectorStore)

    from app.search.hybrid_search import hybrid_search
    results, total = hybrid_search(SearchQuery(keyword="钢筋"))

    assert total == 1
    assert results[0]["id"] == cid
    assert results[0]["_distance"] == 0.3, "多块时必须取最小距离（最优块）"
