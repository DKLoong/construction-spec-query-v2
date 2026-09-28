"""健康检查逻辑测试（检查项判定 + 修复动作）"""
import json
from app.database import init_db, get_db
from app.search.tokenize import build_search_text


def test_clause_id_read_is_column_scoped():
    """收集 clause_id 不得整表物化（含 embedding 列）。

    C-9：断言口径与实现一致——实现走 `lance.dataset(...).to_table(columns=[...])`，
    源码里不再出现 `to_arrow()`，故本断言原样成立。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app/maintenance/health_check.py").read_text(encoding="utf-8")
    assert "to_arrow()" not in src, "改为 iter_clause_ids()（只读 clause_id 列）"


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
             build_search_text("5.1.1", "正常", "正常内容")[0]),
        )
        # 孤立条文：parent_clause 指向不存在的 id
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, parent_clause, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.2", "孤立内容", 99999, build_search_text("5.1.2", "", "孤立内容")[0]),
        )
        # 空内容条文
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, search_text)
               VALUES (?,?,?,?)""",
            (spec_id, "5.1.3", "   ", build_search_text("5.1.3", "", "")[0]),
        )
        # 分类异常：ai_classified=1 但六维空
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, ai_classified, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.4", "异常内容", 1, build_search_text("5.1.4", "", "异常内容")[0]),
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


def test_fix_issue_fts_mismatch_backfills_breadcrumb_too(monkeypatch, tmp_path):
    """补齐 FTS 时必须**同时**写 breadcrumb 列（FTS 已是两列）。

    只写 search_text 会让该行面包屑列落 NULL —— 检索侧按列权重取不到值，
    且 init_db 的 backfill 门槛（「值相等即跳过」）也判不出差异 ⇒ 不会自愈。
    """
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_bc"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    with get_db() as conn:
        cid = conn.execute("SELECT id FROM clauses WHERE clause_no = '5.1.1'").fetchone()[0]
        conn.execute("UPDATE clauses SET breadcrumb = '5 混凝土 5.1 正常' WHERE id = ?", (cid,))
        conn.execute("DELETE FROM clauses_fts WHERE rowid = ?", (cid,))  # 制造缺失行
    result = fix_issue("fts_mismatch")
    assert result["fixed"] is True
    with get_db() as conn:
        row = conn.execute(
            "SELECT search_text, breadcrumb FROM clauses_fts WHERE rowid = ?", (cid,)
        ).fetchone()
    assert row is not None, "缺失的 FTS 行应被补插"
    assert row["breadcrumb"] == "5 混凝土 5.1 正常", "补插时必须一并写入 breadcrumb 列"
    assert row["search_text"] == build_search_text("5.1.1", "正常", "正常内容")[0]


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


def _downgrade_fts_to_single_column(conn):
    """把两列 FTS 换回旧版单列形态，模拟「未迁移的库」（R18 的崩溃场景）。

    不可用 `init_db()` 构造：迁移里的 `_migrate_search_text` 会把单列形态
    自动重建成两列（这正是生产生命周期内不可达的原因），故只能就地降级。
    """
    conn.execute("DROP TABLE clauses_fts")
    conn.execute("CREATE VIRTUAL TABLE clauses_fts USING fts5(search_text)")
    conn.execute(
        "INSERT INTO clauses_fts(rowid, search_text)"
        " SELECT id, COALESCE(search_text, '') FROM clauses"
    )


def test_fix_issue_fts_mismatch_on_single_column_fts(monkeypatch, tmp_path):
    """R18：未迁移库的 FTS 是单列形态，补插 SQL 若写 breadcrumb 会抛
    `OperationalError: table clauses_fts has no column named breadcrumb`，
    整条修复路径（以及「一键修复全部」）就此中断。

    应用启动即迁移 ⇒ 正常生命周期不可达，但单列库（手工/旧快照）可达，
    故补插必须按列是否存在走两列/单列两种写法。
    """
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_sc"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import fix_issue
    with get_db() as conn:
        _downgrade_fts_to_single_column(conn)
        cid = conn.execute("SELECT id FROM clauses WHERE clause_no = '5.1.1'").fetchone()[0]
        conn.execute("DELETE FROM clauses_fts WHERE rowid = ?", (cid,))  # 制造缺失行
        row = conn.execute("SELECT COUNT(*) FROM clauses_fts").fetchone()[0]
        assert row == 3, "降级后应只剩 3 行（4 条条文删掉 1 行）"

    result = fix_issue("fts_mismatch")  # 修复前：此处抛 OperationalError

    assert result["fixed"] is True
    with get_db() as conn:
        back = conn.execute(
            "SELECT search_text FROM clauses_fts WHERE rowid = ?", (cid,)
        ).fetchone()
    assert back is not None, "单列 FTS 上缺失行同样应被补插"
    assert back["search_text"] == build_search_text("5.1.1", "正常", "正常内容")[0]
