"""向量表收尾压实：唯一实现 + 两个接线点（导入 / 全量重建）

为什么要压实：超长条文按子块写入使行数上升 ~15-18%，而每次 add/delete 都会
产生一个新版本，版本数增长随之加快（真实表曾落到 `rows=73 / version=173`，
读放大与启动开销一起变差）。压实放在导入与全量重建的收尾各做一次。

本文件锁定四条契约：
1. `VectorStore.optimize()` 是**唯一**实现（两个调用点共用，不各自写私有访问）；
2. 压实失败**绝不**抛异常——压实是收尾优化、不是交付物，失败只记 WARNING
   且必须含原因（「绝不抛」之所以可接受，正因为它同时记了日志）；
3. 无表时不告警（新库首次导入前的合法状态，不该有日志噪音）；
4. 两个调用点都在**全部向量写入之后**压实（写入前压实等于白做）。
"""
import logging

from app.database import get_db, init_db
from tests.conftest import setup_search_data

_LOGGER_NAME = "app.search.vector_search"


def _setup_db(monkeypatch, tmp_path, name="opt"):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / f"{name}.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / f"lance_{name}"))
    init_db()


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# ═══════════════════════════════════════════
# 1) 包装器本体
# ═══════════════════════════════════════════

def test_optimize_on_real_table_returns_true_and_keeps_rows(monkeypatch, tmp_path):
    """真实表上调用 LanceDB 的 optimize：返回 True，且不丢行"""
    import numpy as np
    _setup_db(monkeypatch, tmp_path, "real")
    from app.search.vector_search import VectorStore, embedding_schema

    vs = VectorStore()
    tbl = vs.db.create_table("clause_embeddings", schema=embedding_schema(4))
    tbl.add([
        {"clause_id": 1, "spec_id": 1, "text": "a", "dim_scores": "",
         "embedding": np.zeros(4, dtype=np.float32), "chunk_index": 0},
        {"clause_id": 2, "spec_id": 1, "text": "b", "dim_scores": "",
         "embedding": np.zeros(4, dtype=np.float32), "chunk_index": 0},
    ])

    assert vs.optimize() is True, "真实表压实应返回 True"
    assert vs._get_table().count_rows() == 2, "压实不得丢行"


def test_optimize_failure_logs_warning_and_never_raises(monkeypatch, tmp_path, caplog):
    """压实失败：不抛异常、返回 False，且 WARNING 必须含原因（不得静默）"""
    _setup_db(monkeypatch, tmp_path, "boom")
    from app.search.vector_search import VectorStore

    class _BoomTable:
        def optimize(self):
            raise RuntimeError("compaction boom")

    monkeypatch.setattr(VectorStore, "_table_exists", lambda self: True)
    monkeypatch.setattr(VectorStore, "_get_table", lambda self: _BoomTable())
    vs = VectorStore()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert vs.optimize() is False, "压实失败必须返回 False（不得抛异常）"

    msgs = _warnings(caplog)
    assert any("压实" in m and "compaction boom" in m for m in msgs), \
        f"压实失败未记含原因的 WARNING: {msgs}"


def test_optimize_without_table_is_silent_noop(monkeypatch, tmp_path, caplog):
    """无表（尚未写入过向量）不是失败：返回 False 且不发告警"""
    _setup_db(monkeypatch, tmp_path, "empty")
    from app.search.vector_search import VectorStore

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert VectorStore().optimize() is False
    assert not [m for m in _warnings(caplog) if "压实" in m], \
        "无表时不该报「压实失败」（新库首次导入前的合法状态）"


# ═══════════════════════════════════════════
# 2) 接线点：导入（全部向量写入之后）
# ═══════════════════════════════════════════

def test_import_compacts_after_all_vector_writes(monkeypatch, tmp_path):
    """导入路径：压实必须是向量写入序列的**最后一步**"""
    _setup_db(monkeypatch, tmp_path, "imp")
    import app.routes.import_routes as ir
    monkeypatch.setattr(ir, "OUTPUT_DIR", str(tmp_path / "out_imp"))
    monkeypatch.setattr(ir, "UPLOAD_DIR", str(tmp_path / "up_imp"))
    events: list[tuple] = []

    class _Table:
        def add(self, records):
            events.append(("add", len(records)))

    class _VS:
        def _table_exists(self):
            return True

        def _get_table(self):
            return _Table()

        def optimize(self):
            events.append(("optimize", 0))
            return True

    monkeypatch.setattr(ir, "VectorStore", lambda: _VS())
    monkeypatch.setattr("app.ai.embedding.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    ir.progress_store["opt000001"] = {"status": "processing", "progress": 0, "owner": "t"}
    try:
        ir._process_import_phase2(
            "opt000001", "# 第1章\n5.1.1 条文内容测试。\n", "压实测试规范",
            "GB/T 55555-2020", str(tmp_path / "f.md"), "hash_opt", "现行", "")
    finally:
        ir.progress_store.pop("opt000001", None)

    assert events and events[-1][0] == "optimize", \
        f"导入收尾未压实（或压实不在写入之后）: {events}"
    assert sum(n for k, n in events if k == "add") > 0, f"本用例应真的写过向量: {events}"


# ═══════════════════════════════════════════
# 3) 接线点：全量重建（batch_index 之后）
# ═══════════════════════════════════════════

def test_rebuild_compacts_after_batch_index(monkeypatch, tmp_path):
    """全量重建：batch_index 之后压实，且不改变 done 终态"""
    import app.routes.maintenance_routes as mr
    _setup_db(monkeypatch, tmp_path, "rb")
    with get_db() as conn:
        setup_search_data(conn)

    from app.search.vector_search import VectorStore
    events: list[str] = []

    def fake_batch_index(self, records, batch_size=32, progress_cb=None):
        events.append("batch_index")

    def fake_optimize(self):
        events.append("optimize")
        return True

    monkeypatch.setattr(VectorStore, "batch_index", fake_batch_index)
    monkeypatch.setattr(VectorStore, "optimize", fake_optimize)
    mr._run_rebuild("rb_opt")

    assert events == ["batch_index", "optimize"], f"重建收尾未按序压实: {events}"
    assert mr.rebuild_progress["rb_opt"]["status"] == "done", mr.rebuild_progress["rb_opt"]
