import pytest

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
    results, total = search_clauses(SearchQuery(dim5_location=["屋面"]))
    assert total >= 1
    assert results[0]["dim5_location"] == "屋面"


def test_search_combined(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        setup_search_data(conn)
    results, total = search_clauses(SearchQuery(keyword="混凝土", dim6_material=["模板"]))
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
             build_search_text("1.0.1", "其他标题", "本条内容包含关键词 钢筋 需要命中")[0]),
        )
        # title 命中（title 分词含关键词）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.0.1", "钢筋 验收", "普通内容",
             build_search_text("2.0.1", "钢筋 验收", "普通内容")[0]),
        )
        # clause_no 精确命中（最高优先级）
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "钢筋", "其他标题", "普通内容",
             build_search_text("钢筋", "其他标题", "普通内容")[0]),
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
             build_search_text("1.0.1", "其他标题", "钢筋 钢筋 钢筋 混凝土")[0]),
        )
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text) VALUES (?, ?, ?, ?, ?)",
            (spec_id, "2.0.1", "钢筋 验收", "普通内容",
             build_search_text("2.0.1", "钢筋 验收", "普通内容")[0]),
        )
        id_b = conn.execute("SELECT id FROM clauses WHERE clause_no='2.0.1'").fetchone()[0]

    results, total = search_clauses(SearchQuery(keyword="钢筋"))
    assert total == 2
    assert results[0]["id"] == id_b, "title 命中应排在 content 命中（即使 bm25 词频更高）之前"


# ═══════════════════════════════════════════
# Task 5：bm25 权重走绑定参数 + 权重=0 的列限定 MATCH
# ═══════════════════════════════════════════

def _fts():
    """内存 FTS5 两列表（与 clauses_fts 同构）：行 1 仅面包屑命中，行 2 正文命中。"""
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.execute("CREATE VIRTUAL TABLE f USING fts5(search_text, breadcrumb)")
    c.execute("INSERT INTO f(rowid, search_text, breadcrumb) VALUES"
              " (1, '钢筋 连接 要求', '6 接头 的 现场 加工 6.3 接头 安装')")
    c.execute("INSERT INTO f(rowid, search_text, breadcrumb) VALUES"
              " (2, '接头 安装 应 满足 强度 要求', '1 总则')")
    return c


def test_weight_zero_does_not_disable_breadcrumb_recall():
    """记录事实：仅把权重置 0 关不掉 breadcrumb 列的召回（本条锁定我们为何需要列限定）

    实测 sqlite 3.50.4：`bm25(f, 1.0, 0.0)` 只是缩放评分，**不改变召回**——
    FTS5 的 MATCH 与列无关，仅命中 breadcrumb 的行仍被返回。
    """
    rows = [r[0] for r in _fts().execute(
        "SELECT rowid FROM f WHERE f MATCH ? ORDER BY bm25(f, 1.0, 0.0)",
        ('"接头" AND "安装"',)).fetchall()]
    assert 1 in rows, "若此断言变化，说明 SQLite 语义变了——需重新评估开关实现"


def test_column_scoped_match_excludes_breadcrumb_only_rows():
    """列限定 MATCH 才能把仅命中 breadcrumb 的行排除（这是权重=0 的实现依据）"""
    rows = [r[0] for r in _fts().execute(
        "SELECT rowid FROM f WHERE f MATCH ?", ('search_text : ("接头" AND "安装")',)
    ).fetchall()]
    assert rows == [2], "列限定后，仅命中 breadcrumb 的行 1 必须被排除"


def test_scope_helper_wraps_expression():
    """helper 必须整体加括号：扩展器产出的表达式本身含嵌套括号"""
    from app.search.sql_search import _scope_match_to_search_text
    assert _scope_match_to_search_text('"a" AND ("b" OR "c")') == 'search_text : ("a" AND ("b" OR "c"))'


def test_search_uses_bound_parameter_for_bm25_weight():
    """排序 SQL 必须用绑定参数而非字面量（实测绑定参数可用，字面量是伪风险且是注入面）"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app/search/sql_search.py").read_text(encoding="utf-8")
    assert "bm25(clauses_fts, 1.0, ?)" in src
    assert "bm25(clauses_fts, 1.0, %" not in src and "float(" not in src.split("bm25")[-1][:80]


_BREADCRUMB_WEIGHT_KEY = "search.breadcrumb_weight"


def _seed_breadcrumb_case(monkeypatch, tmp_path, name):
    """A 仅正文命中「钢筋」；B 仅面包屑命中「接头/安装」。返回 (id_a, id_b)。"""
    import app.database as _db
    import app.params.registry as registry
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / name))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_bc"))
    registry.clear_param_cache()
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        st_a = build_search_text("1.0.1", "钢筋 验收", "钢筋 进场 应 检验")
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text, breadcrumb) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "1.0.1", "钢筋 验收", "钢筋 进场 应 检验", st_a[0], st_a[1]))
        id_a = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        st_b = build_search_text("6.3.1", "现场加工", "本条 正文 无 关键词",
                                 "6 混凝土分项工程 > 6.3 接头安装")
        assert "接头" in st_b[1].split() and "安装" in st_b[1].split(), "夹具守卫：面包屑须含接头/安装"
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text, breadcrumb) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "6.3.1", "现场加工", "本条 正文 无 关键词", st_b[0], st_b[1]))
        id_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return id_a, id_b


def _set_breadcrumb_weight(value) -> None:
    """把权重写进 settings（消费方按 key 读取），并清参数缓存"""
    import app.params.registry as registry
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     (_BREADCRUMB_WEIGHT_KEY, str(value)))
    registry.clear_param_cache()


def test_breadcrumb_weight_zero_excludes_breadcrumb_only_rows(monkeypatch, tmp_path):
    """权重=0 → 仅面包屑命中的条文必须被排除（0 是「真的关闭」，不是「排得低」）"""
    _, id_b = _seed_breadcrumb_case(monkeypatch, tmp_path, "bc0.db")
    _set_breadcrumb_weight(0)
    results, total = search_clauses(SearchQuery(keyword="接头安装"))
    assert id_b not in [r["id"] for r in results], "权重 0 时面包屑列必须完全退出检索"
    assert total == 0 and results == []


def test_breadcrumb_weight_enabled_recalls_breadcrumb_only_rows(monkeypatch, tmp_path):
    """权重>0（注册表默认 0.3）→ 面包屑命中仍召回（防「参数未注册 ⇒ 恒 0 ⇒ 功能静默失效」）"""
    _, id_b = _seed_breadcrumb_case(monkeypatch, tmp_path, "bc1.db")
    results, total = search_clauses(SearchQuery(keyword="接头安装"))
    assert total == 1 and results[0]["id"] == id_b


def test_breadcrumb_weight_zero_or_fallback_body_hit_still_recalls(monkeypatch, tmp_path):
    """权重 0 下 OR 兜底路径的列限定表达式必须可用（且正文部分命中仍召回）

    B 的正文只含「接头」、面包屑含「安装」：AND 在全列命中（权重>0）→ 无 OR 兜底；
    权重 0 时列限定 AND 落空 → 触发 OR 兜底，此时 MATCH 串是**列限定 + OR**，
    若 helper 漏加外层括号这里会 FTS5 语法报错。
    """
    import app.database as _db
    import app.params.registry as registry
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "bc3.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_bc3"))
    registry.clear_param_cache()
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        st = build_search_text("6.3.1", "现场加工", "接头 加工 要求",
                               "6 混凝土分项工程 > 6.3 安装")
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text, breadcrumb) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "6.3.1", "现场加工", "接头 加工 要求", st[0], st[1]))
        id_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    _set_breadcrumb_weight(0)
    results, total = search_clauses(SearchQuery(keyword="接头安装"))
    assert total == 1, "OR 兜底：正文命中「接头」仍应召回（列限定表达式须语法有效）"
    assert results[0]["id"] == id_b


def test_breadcrumb_weight_from_settings_takes_effect(monkeypatch, tmp_path):
    """DB settings 覆盖即时生效：同一夹具下 0 与 0.5 召回结果不同（参数真的被读取）"""
    _, id_b = _seed_breadcrumb_case(monkeypatch, tmp_path, "bc2.db")
    _set_breadcrumb_weight(0)
    results_zero, total_zero = search_clauses(SearchQuery(keyword="接头安装"))
    _set_breadcrumb_weight(0.5)
    results_half, total_half = search_clauses(SearchQuery(keyword="接头安装"))
    assert total_zero == 0 and results_zero == []
    assert total_half == 1 and results_half[0]["id"] == id_b


# ═══════════════════════════════════════════
# U16：源文件写成「词内空格」的节名（`1 总 则`）也必须能被自然名召回
# ═══════════════════════════════════════════

#: （节名原文, 自然查询词）——都是 CJJ2 夹具里逐字存在的带词内空格节名
_SPACED_SECTION_CASES = [
    ("1 总 则", "总则"),
    ("6 钢 筋", "钢筋"),
    ("12 支 座", "支座"),
    ("10 基 础 > 10.4 沉 井", "沉井"),
]


def _seed_spaced_section_case(monkeypatch, tmp_path, name, section_path):
    """建库并在**只有面包屑**含该节名的条文上插一条，返回 (clause_id, breadcrumb, search_text)。

    正文与标题刻意不含查询词的任何 token —— 唯一召回通道是 breadcrumb 列，
    故「查不到」只可能是面包屑没产出整词（这正是 U16 的缺陷形态）。
    """
    import app.database as _db
    import app.params.registry as registry
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / name))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / (name + "_lance")))
    registry.clear_param_cache()
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB-TEST', '测试')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        st, bc = build_search_text("6.3.1", "现场加工", "本条只讲施工要点，不重复节名。", section_path)
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content, search_text, breadcrumb) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (spec_id, "6.3.1", "现场加工", "本条只讲施工要点，不重复节名。", st, bc))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return cid, bc, st


@pytest.mark.parametrize("section_path, word", _SPACED_SECTION_CASES)
def test_spaced_section_name_is_recallable_by_natural_name(monkeypatch, tmp_path, section_path, word):
    """节名原文带词内空格（`1 总 则`）时，查自然名「总则」必须经面包屑列召回

    缺陷形态：面包屑直接分词 → jieba 得 `1 / 总 / 则`，查「总则」在任何权重下都 0 结果。
    修法（D13）只在派生文本（面包屑列）折叠词内空白，`section_path` 原文不动。
    """
    cid, bc, st = _seed_spaced_section_case(
        monkeypatch, tmp_path, f"u16_{word}.db", section_path)
    # 夹具守卫：正文/标题切不出查询词 → 命中只可能来自面包屑列（否则本用例测不到该通道）
    assert word not in st.split(), f"夹具守卫：search_text 已含 {word!r}，面包屑通道测不到"
    results, total = search_clauses(SearchQuery(keyword=word))
    assert total == 1 and results[0]["id"] == cid, \
        f"节名 {section_path!r} 无法被自然名 {word!r} 召回（total={total}，面包屑={bc!r}）"


def test_spaced_section_name_recall_holds_at_disabled_weight_boundary(monkeypatch, tmp_path):
    """边界：权重 = 0（面包屑列退出检索）时**不应**召回；权重恢复后仍能召回

    两态对照，证明上面那条召回真的来自面包屑列，而不是别处漏进的 token。
    """
    section_path, word = _SPACED_SECTION_CASES[0]
    cid, _bc, _st = _seed_spaced_section_case(monkeypatch, tmp_path, "u16_w0.db", section_path)
    _set_breadcrumb_weight(0)
    results_off, total_off = search_clauses(SearchQuery(keyword=word))
    assert total_off == 0 and results_off == [], "权重 0 时面包屑列应完全退出检索"
    _set_breadcrumb_weight(0.3)
    results_on, total_on = search_clauses(SearchQuery(keyword=word))
    assert total_on == 1 and results_on[0]["id"] == cid
