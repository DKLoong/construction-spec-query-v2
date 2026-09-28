"""维护路由健康检查端点测试"""
import json
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path, name="m.db"):
    db_path = tmp_path / name
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / f"lance-{name}"))
    init_db()
    return db_path


def test_health_check_endpoint_returns_result(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)
    resp = auth_client.post("/maintenance/health-check")
    assert resp.status_code == 200
    assert "孤立无父级条文" in resp.text  # 渲染结果标签


def test_fix_all_endpoint(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/maintenance/fix-all")
    assert resp.status_code == 200


def test_health_check_writes_snapshot(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    auth_client.post("/maintenance/health-check")
    with get_db() as conn:
        row = conn.execute(
            "SELECT result FROM health_check_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None and "checks" in row["result"]


def test_rebuild_start_returns_task_id(auth_client, monkeypatch, tmp_path):
    """POST rebuild 立即返回 task_id（后台线程执行，不阻塞请求）"""
    import app.routes.maintenance_routes as mr
    monkeypatch.setattr(mr, "_run_rebuild", lambda tid: None)  # 不真实跑后台
    _setup(monkeypatch, tmp_path)
    resp = auth_client.post("/maintenance/rebuild-vectors")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"].startswith("rb")


def test_rebuild_progress_endpoint(auth_client, monkeypatch, tmp_path):
    """GET rebuild-progress 返回进度 JSON，未知任务回 unknown"""
    import app.routes.maintenance_routes as mr
    _setup(monkeypatch, tmp_path)
    mr.rebuild_progress["rb_test"] = {"status": "done", "progress": 100,
                                      "message": "重建完成"}
    resp = auth_client.get("/maintenance/rebuild-progress/rb_test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done" and data["progress"] == 100
    resp2 = auth_client.get("/maintenance/rebuild-progress/not-exist")
    assert resp2.json()["status"] == "unknown"


def test_run_rebuild_updates_progress(monkeypatch, tmp_path):
    """_run_rebuild 通过 batch_index progress_cb 上报，完成后置 done 100%"""
    import app.routes.maintenance_routes as mr
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)  # 1 spec + 3 clauses
    from app.search.vector_search import VectorStore

    def fake_batch_index(self, records, batch_size=32, progress_cb=None):
        if progress_cb:
            progress_cb(len(records), len(records))  # 模拟一次全部完成
    monkeypatch.setattr(VectorStore, "batch_index", fake_batch_index)
    mr._run_rebuild("rb_sync")
    assert mr.rebuild_progress["rb_sync"]["status"] == "done"
    assert mr.rebuild_progress["rb_sync"]["progress"] == 100


def test_run_rebuild_embeds_section_path(monkeypatch, tmp_path):
    """全量重建写进向量的文本必须含面包屑，位置在条文号之后、标题之前。

    重建是本批「存量向量补面包屑」的**唯一**通道（U7 的重建门禁会调它）：
    这里漏传第 6 参是静默的，会让整库重建产出与导入路径不一致的向量。
    """
    import app.routes.maintenance_routes as mr
    _setup(monkeypatch, tmp_path, name="rb_sp.db")
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50010', '混凝土规范')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, section_path)
               VALUES (?, ?, ?, ?, ?)""",
            (spec_id, "5.1.1", "模板", "内容", "5 混凝土分项工程 > 5.1 模板"),
        )
    from app.search.vector_search import VectorStore
    records: list[dict] = []

    def fake_batch_index(self, recs, batch_size=32, progress_cb=None):
        records.extend(recs)

    monkeypatch.setattr(VectorStore, "batch_index", fake_batch_index)
    mr._run_rebuild("rb_sp")
    assert mr.rebuild_progress["rb_sp"]["status"] == "done", mr.rebuild_progress["rb_sp"]
    assert len(records) == 1
    assert records[0]["text"] == (
        "GB 50010 混凝土规范 [5.1.1] 5 混凝土分项工程 > 5.1 模板 模板 内容")


def test_pending_flag_endpoint_is_pure_read(auth_client, monkeypatch, tmp_path):
    """红点端点：返回 {red, yellow} 且不写 system_logs / 快照

    该端点被前端「加载一次 + 事件 + 5 分钟兜底」调用，一旦写入会随轮询累积；
    故契约上锁死返回结构与零副作用两点。
    """
    _setup(monkeypatch, tmp_path, name="badge.db")
    with get_db() as conn:
        setup_search_data(conn)
        logs_before = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
        snaps_before = conn.execute(
            "SELECT COUNT(*) FROM health_check_snapshots").fetchone()[0]

    resp = auth_client.get("/maintenance/pending-flag")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"red", "yellow", "labels"}
    assert isinstance(body["red"], list) and isinstance(body["yellow"], list)
    with get_db() as conn:
        logs_after = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
        snaps_after = conn.execute(
            "SELECT COUNT(*) FROM health_check_snapshots").fetchone()[0]
    assert (logs_after, snaps_after) == (logs_before, snaps_before), \
        f"红点端点写入了审计记录：{(logs_before, snaps_before)} → {(logs_after, snaps_after)}"
