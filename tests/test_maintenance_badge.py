"""宫格「维护」红点数据源测试

红点端点是纯读接口（前端 5 分钟兜底周期 + 事件驱动调用，见 T2），必须满足两条硬约束：

1. **不落库**——绝不写 system_logs / health_check_snapshots。健康检查本体每次调用
   都会写一条日志+一条快照，若红点端点复用它，日志表会被轮询撑爆
   （同 rule_pending.pending_counts() 的设计约束）。
2. **分级口径与维护页逐字一致**——否则会出现「红点亮着、点进去全 ✅」的矛盾。

分级：red = 可立即处理或功能已失效；yellow = 功能降级或仅报告。
"""
import pyarrow as pa

from app.database import init_db, get_db
from app.search.tokenize import build_search_text


def _setup_db(monkeypatch, tmp_path):
    """构造带 1 条干净条文 + 各类脏数据的库（同 test_health_check 夹具口径）"""
    db_path = tmp_path / "badge.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        # 本用例需构造悬空 parent_clause 脏数据，而 get_connection 开启
        # PRAGMA foreign_keys=ON，正常插入会触发 FK 约束失败；故先关外键。
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?,?,?)",
            ("GB 50010", "混凝土规范", "现行"),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 干净条文（分类完整、非空内容、无悬空父级）
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, dim4_specialty,
               dim5_location, dim6_material, ai_classified, search_text) VALUES (?,?,?,?,?,?,?,?,?)""",
            (spec_id, "5.1.1", "正常", "正常内容", "结构专业", "主体结构", "钢筋", 1,
             build_search_text("5.1.1", "正常", "正常内容")[0]),
        )
    return spec_id


def _add_orphan_parent(spec_id):
    with get_db() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, parent_clause, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.2", "孤立内容", 99999, build_search_text("5.1.2", "", "孤立内容")[0]),
        )


def _add_empty_content(spec_id):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, search_text)
               VALUES (?,?,?,?)""",
            (spec_id, "5.1.3", "   ", build_search_text("5.1.3", "", "")[0]),
        )


def _add_bad_classification(spec_id):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, content, ai_classified, search_text)
               VALUES (?,?,?,?,?)""",
            (spec_id, "5.1.4", "异常内容", 1, build_search_text("5.1.4", "", "异常内容")[0]),
        )


def _audit_counts() -> tuple[int, int]:
    """(system_logs 条数, health_check_snapshots 条数)"""
    with get_db() as conn:
        logs = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
        snaps = conn.execute("SELECT COUNT(*) FROM health_check_snapshots").fetchone()[0]
    return logs, snaps


def _make_vector_table(lance_path, clause_ids):
    """建一个含指定 clause_id 的向量表（固定 4 维零向量，避免加载 BGE 模型）"""
    import lancedb
    db = lancedb.connect(str(lance_path))
    tbl = db.create_table("clause_embeddings", schema=pa.schema([
        pa.field("clause_id", pa.int64()),
        pa.field("spec_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), 4)),
        pa.field("dim_scores", pa.string()),
    ]))
    if clause_ids:
        import numpy as np
        tbl.add([{"clause_id": cid, "spec_id": 1, "text": "t",
                  "embedding": np.zeros(4, dtype=np.float32), "dim_scores": ""}
                 for cid in clause_ids])


def _all_clause_ids():
    with get_db() as conn:
        return [r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()]


def test_pending_flag_silent_on_clean_db(monkeypatch, tmp_path):
    """正常：库干净 + 向量表齐全 → 红黄皆空（不误报）"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_ok"))
    _setup_db(monkeypatch, tmp_path)
    _make_vector_table(tmp_path / "lance_ok", _all_clause_ids())
    from app.maintenance.health_check import pending_flag

    flag = pending_flag()

    assert flag["red"] == []
    assert flag["yellow"] == []


def test_pending_flag_marks_red_items(monkeypatch, tmp_path):
    """红点：可立即处理的项（孤立父级 / 分类异常）逐项点名"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_red"))
    spec_id = _setup_db(monkeypatch, tmp_path)
    _add_orphan_parent(spec_id)
    _add_bad_classification(spec_id)
    from app.maintenance.health_check import pending_flag

    flag = pending_flag()

    assert "orphan_parent" in flag["red"]
    assert "bad_classification" in flag["red"]
    assert "empty_content" not in flag["red"]
    # 中文名随判定下发（前端 title 直接用，不在 JS 里复制 LABELS）
    assert flag["labels"]["orphan_parent"] == "孤立无父级条文"
    assert flag["labels"]["bad_classification"] == "分类标签异常"


def test_pending_flag_marks_yellow_items(monkeypatch, tmp_path):
    """黄点：仅报告类（空内容不自动删）归黄，不占红点"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_yellow"))
    spec_id = _setup_db(monkeypatch, tmp_path)
    _add_empty_content(spec_id)
    _make_vector_table(tmp_path / "lance_yellow", _all_clause_ids())   # 向量齐全，隔离出黄点
    from app.maintenance.health_check import pending_flag

    flag = pending_flag()

    assert "empty_content" in flag["yellow"]
    assert flag["red"] == []


def test_pending_flag_vector_table_missing_is_red(monkeypatch, tmp_path):
    """边界：向量表不存在（待重建）归红点——用户可立即点重建处理"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / "lance_none"))
    _setup_db(monkeypatch, tmp_path)
    from app.maintenance.health_check import pending_flag

    flag = pending_flag()

    assert "vector_missing" in flag["red"]


def test_pending_flag_vector_read_failure_is_red(monkeypatch, tmp_path):
    """异常：向量表读取抛错同样归红点（需人工查日志，比静默更该提示）"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_err"))
    _setup_db(monkeypatch, tmp_path)
    import app.search.vector_search as vsmod

    class _Broken:
        def _table_exists(self):
            return True

        def _get_table(self):
            raise RuntimeError("lance read broken")

    monkeypatch.setattr(vsmod, "VectorStore", _Broken)
    from app.maintenance.health_check import pending_flag

    flag = pending_flag()

    assert "vector_missing" in flag["red"]


def test_pending_flag_writes_no_audit_rows(monkeypatch, tmp_path):
    """硬约束：红点端点被周期轮询，绝不可写 system_logs / 快照

    这是本 Task 存在的核心风险点——复用 run_health_check() 会让日志表被轮询撑爆。
    """
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_audit"))
    spec_id = _setup_db(monkeypatch, tmp_path)
    _add_orphan_parent(spec_id)
    from app.maintenance.health_check import pending_flag

    before = _audit_counts()
    for _ in range(3):          # 模拟多次轮询
        pending_flag()
    after = _audit_counts()

    assert after == before, f"红点端点写入了审计记录：{before} → {after}"


def test_pending_flag_agrees_with_health_check(monkeypatch, tmp_path):
    """口径一致：红黄分级必须与维护页 run_health_check 的判定同源

    防「红点亮着、点进去全 ✅」的矛盾（谓词漂移是本仓踩过的坑）。
    """
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_sync"))
    spec_id = _setup_db(monkeypatch, tmp_path)
    _add_orphan_parent(spec_id)
    _add_empty_content(spec_id)
    from app.maintenance.health_check import pending_flag, run_health_check

    flag = pending_flag()
    checks = {c["key"]: c for c in run_health_check()["checks"]}

    # 红点里点名的每一项，维护页对应项必须确实非正常
    for key in flag["red"]:
        sev = checks[key]["severity"]
        assert sev not in ("ok",), f"{key} 在红点上但维护页判定为 ok"
    # 红点为空时，所有可立即处理项在维护页也必须全 ok
    fixable_red = [k for k in ("orphan_parent", "bad_classification",
                               "vector_orphan", "fts_mismatch")
                   if k in flag["red"]]
    for key in fixable_red:
        assert checks[key]["count"] > 0


def test_pending_flag_distinguishes_empty_table_from_absent(monkeypatch, tmp_path):
    """边界：向量表存在但为空 ≠ 表不存在——两者都归红，但语义分支必须区分

    表不存在 = 待重建（severity=rebuild，应引导全量重建）；
    表存在而缺条 = 缺 N 条（可单项补齐）。红点不能把两者混为一谈。
    """
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_empty"))
    _setup_db(monkeypatch, tmp_path)
    _make_vector_table(tmp_path / "lance_empty", [])        # 建空表
    from app.maintenance.health_check import pending_flag, run_health_check

    flag = pending_flag()
    checks = {c["key"]: c for c in run_health_check()["checks"]}

    assert "vector_missing" in flag["red"]
    assert checks["vector_missing"]["severity"] != "rebuild", "走成了「缺表」分支"
    assert checks["vector_missing"]["count"] == 1
