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
from pathlib import Path

from app.database import get_db, init_db
from tests.conftest import setup_search_data

_LOGGER_NAME = "app.search.vector_search"


def _retained_versions(vs) -> int:
    """向量表目录下留存的版本清单数（= 尚未被 cleanup 掉的版本数）。

    LanceDB 每个版本一个 `_versions/<n>.manifest`；`table.version` 只增不减，
    故「版本是否真的被清理」只能数留存文件，不能读 version。
    """
    vdir = Path(vs.db.uri) / "clause_embeddings.lance" / "_versions"
    return len(list(vdir.glob("*.manifest")))


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


def test_optimize_bounds_retained_versions(monkeypatch, tmp_path):
    """压实必须**真的约束版本数**，而不是「调用成功」而已。

    lancedb 0.17 的 `optimize()` 默认 `cleanup_older_than=None` ⇒ 保留期 **7 天**：
    数据文件会合并，但近期版本文件照旧留存、版本数不下降。而本批要治的版本膨胀是
    **一次会话内**发生的（真实表 rows=73 / version=173），7 天保留期对它完全无效。
    故 wrapper 传 `cleanup_older_than=timedelta(0)`（只保留最新版本）。

    本机 lancedb 0.17.0 实测（同表、同一串操作）：不传参 → 留存版本清单 9 个；
    传 0 → 1 个（数据文件 5 → 1）。本用例把这条实测钉在真实表上：先制造多版本，
    压实后要求留存版本收缩到 1（回归成不传参时本断言必红）。
    """
    _setup_db(monkeypatch, tmp_path, "ver")
    # 走真实写入原语（每条约 delete+add，各产生一个版本），不加载真实模型
    monkeypatch.setattr("app.search.vector_search.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    from app.search.vector_search import VectorStore

    vs = VectorStore()
    for cid in range(1, 5):
        vs.index_clause(cid, 1, f"条文文本 {cid}")

    before = _retained_versions(vs)
    assert before > 1, f"夹具没制造出多版本，本用例判定力为零: {before}"
    assert vs._get_table().count_rows() == 4, "夹具应有 4 条向量行"

    assert vs.optimize() is True
    after = _retained_versions(vs)
    assert after == 1, (
        f"压实未清理过期版本（默认 7 天保留期下不会下降）: 留存版本 {before} → {after}；"
        f"wrapper 必须传 cleanup_older_than=timedelta(0)")
    assert vs._get_table().count_rows() == 4, "清理旧版本不得丢行"


def test_optimize_failure_logs_warning_and_never_raises(monkeypatch, tmp_path, caplog):
    """压实失败：不抛异常、返回 False，且 WARNING 必须含原因（不得静默）"""
    _setup_db(monkeypatch, tmp_path, "boom")
    from app.search.vector_search import VectorStore

    class _BoomTable:
        def optimize(self, **kwargs):
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
