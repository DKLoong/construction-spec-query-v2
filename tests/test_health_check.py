"""健康检查逻辑测试（检查项判定 + 修复动作）"""
import json
from app.database import init_db, get_db
from app.search.tokenize import build_search_text


def _setup_db(monkeypatch, tmp_path):
    db_path = tmp_path / "hc.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        # 本用例需构造「悬空 parent_clause」脏数据，而 get_connection 开启了
        # PRAGMA foreign_keys=ON（parent_clause REFERENCES clauses(id)），正常插入会触发
        # FK 约束失败；故在此连接事务开始前先关闭外键约束，以模拟历史遗留脏数据。
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?,?,?)",
            ("GB 50010", "混凝土规范", "现行"),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 正常条文（带分类）
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, dim4_specialty,
               dim5_location, dim6_material, ai_classified, search_text) VALUES (?,?,?,?,?,?,?,?,?)""",
            (spec_id, "5.1.1", "正常", "正常内容", "结构专业", "主体结构", "钢筋", 1,
             build_search_text("5.1.1", "正常", "正常内容")),
        )
        # 孤立条文：parent_clause 指向不存在的 id
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, parent_clause, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.2", "孤立内容", 99999, build_search_text("5.1.2", "", "孤立内容")),
        )
        # 空内容条文
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, search_text)
               VALUES (?,?,?,?)""",
            (spec_id, "5.1.3", "   ", build_search_text("5.1.3", "", "")),
        )
        # 分类异常：ai_classified=1 但六维空
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, ai_classified, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.4", "异常内容", 1, build_search_text("5.1.4", "", "异常内容")),
        )
    return db_path


def test_run_health_check_detects_issues(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import run_health_check
    result = run_health_check()
    checks = {c["key"]: c for c in result["checks"]}
    assert checks["orphan_parent"]["count"] == 1
    assert checks["empty_content"]["count"] == 1
    assert checks["bad_classification"]["count"] == 1
    assert checks["vector_orphan"]["fixable"] is True
    assert checks["fts_mismatch"]["count"] == 0  # 全部 search_text 已同步 FTS
    # 写快照表
    with get_db() as conn:
        snap = conn.execute("SELECT result FROM health_check_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    assert snap is not None and "orphan_parent" in snap["result"]


def test_fix_issue_orphan_parent(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance2"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    fix_issue("orphan_parent")
    with get_db() as conn:
        orphan = conn.execute(
            "SELECT parent_clause FROM clauses WHERE clause_no = '5.1.2'"
        ).fetchone()
    assert orphan["parent_clause"] is None  # 悬空引用已置空


def test_fix_issue_bad_classification(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance3"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    fix_issue("bad_classification")
    with get_db() as conn:
        row = conn.execute(
            "SELECT ai_classified, needs_review FROM clauses WHERE clause_no = '5.1.4'"
        ).fetchone()
    assert row["ai_classified"] == 0
    assert row["needs_review"] == 1


def test_fix_issue_empty_content_not_fixable(monkeypatch, tmp_path):
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance4"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    result = fix_issue("empty_content")
    assert result["fixed"] is False  # 仅报告不自动删


def test_vector_missing_table_missing_not_fixable(monkeypatch, tmp_path):
    """向量表不存在时：vector_missing 不报可修复 N 条，severity=rebuild（待重建），
    单项修复返回「请重建」"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_none"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import run_health_check, fix_issue
    result = run_health_check()
    checks = {c["key"]: c for c in result["checks"]}
    vm = checks["vector_missing"]
    assert vm["count"] == 0  # 表不存在按 0 计，不误报「可修复 N 条」
    assert vm["fixable"] is False
    assert vm["severity"] == "rebuild"
    assert vm.get("count_text") == "待重建"
    r = fix_issue("vector_missing")
    assert r["fixed"] is False
    assert "重建" in r["detail"]  # 提示走「重建向量索引」而非「已补齐」
