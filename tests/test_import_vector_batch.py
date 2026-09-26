"""导入写向量索引必须是批量，不得逐条 add

背景：`_process_import_phase2` 原先在「向量表已存在」分支里**逐条** `add([单行])`，
而「表不存在」分支才批量。由于第一本规范导入时走的是后者，逐条路径长期未暴露——
直到第二本起才开始逐条。实测（512 维、1011 行、临时目录）：批量 0.3 秒 vs
逐条+每次重开表 12.1 秒，**慢 40 倍**；且每次 add 产生一个新版本，真实表因此
落到 `rows=73 / version=173`。

违反全局 CLAUDE.md 1.3「循环内部禁止执行 IO 操作」与「批量数据操作必须使用
批量提交接口」。本文件锁定「写入次数随条数增长而非等于条数」这一机制。

注：逐条写入**不是**导入卡死的原因（卡死见 tests/test_confirm_transport.py），
它只是导入变慢与版本膨胀的成因。
"""
from app.database import init_db
from app.routes import import_routes as ir

# 生成的条文条数：远大于批大小，才能把「逐条」与「分批」区分开
_N_CLAUSES = 1000
# 判定阈值：分期写入的次数应远小于条数。留足余量以免与批大小常量耦合。
_MAX_ACCEPTABLE_ADDS = 20


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "batch.db"))
    monkeypatch.setattr(ir, "OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(ir, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()


def _many_clause_md(n=_N_CLAUSES):
    """生成 n 条正文型条文（编号带点、尾随句号 → 正文型，不建父级）"""
    return "".join(f"{i + 1}.0.1 本条为批量写入测试条文内容。\n" for i in range(n))


class _FakeTable:
    """只记录 add 的批量大小的替身表"""

    def __init__(self, sink):
        self._sink = sink

    def add(self, records):
        self._sink.append(len(records))


class _FakeVS:
    """向量表「已存在」的替身——正是原先走逐条分支的情形"""

    def __init__(self, sink):
        self._sink = sink

    def _table_exists(self):
        return True

    def _get_table(self):
        return _FakeTable(self._sink)


def _run_import(monkeypatch, tmp_path, task_id):
    _setup(monkeypatch, tmp_path)
    adds: list[int] = []
    monkeypatch.setattr(ir, "VectorStore", lambda: _FakeVS(adds))
    # 不加载真实 BGE 模型：只要维度一致即可，本测试关心的是写入次数
    monkeypatch.setattr("app.ai.embedding.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    ir.progress_store[task_id] = {"status": "processing", "progress": 0, "owner": "t"}
    try:
        ir._process_import_phase2(
            task_id, _many_clause_md(), "批量测试规范", "GB/T 99999-2020",
            str(tmp_path / "f.md"), "hash_batch", "现行", "",
        )
    finally:
        ir.progress_store.pop(task_id, None)
    return adds


def test_vector_write_is_batched_not_per_row(monkeypatch, tmp_path):
    """向量表已存在时 → 写入次数远小于条文数（逐条实现下会等于 1000）"""
    adds = _run_import(monkeypatch, tmp_path, "bt000001")
    assert sum(adds) == _N_CLAUSES, f"写入行数不等于条文数: {sum(adds)} != {_N_CLAUSES}"
    assert len(adds) <= _MAX_ACCEPTABLE_ADDS, (
        f"写入被拆成 {len(adds)} 次（条文 {_N_CLAUSES} 条）——"
        f"疑似退回逐条 add；每次 add 都会产生新版本并使后端变慢"
    )
    assert all(n > 0 for n in adds), f"存在空批次: {adds}"


def test_vector_write_covers_every_clause_exactly_once(monkeypatch, tmp_path):
    """分批不得丢行或重复：各行 clause_id 必须唯一且覆盖全部条文"""
    _setup(monkeypatch, tmp_path)
    ids: list[int] = []

    class _IdTable:
        def add(self, records):
            ids.extend(r["clause_id"] for r in records)

    class _IdVS:
        def _table_exists(self):
            return True

        def _get_table(self):
            return _IdTable()

    monkeypatch.setattr(ir, "VectorStore", lambda: _IdVS())
    monkeypatch.setattr("app.ai.embedding.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    ir.progress_store["bt000002"] = {"status": "processing", "progress": 0, "owner": "t"}
    try:
        ir._process_import_phase2(
            "bt000002", _many_clause_md(), "批量测试规范", "GB/T 99999-2021",
            str(tmp_path / "f2.md"), "hash_batch2", "现行", "",
        )
    finally:
        ir.progress_store.pop("bt000002", None)

    assert len(ids) == _N_CLAUSES, f"写入 {len(ids)} 行，应为 {_N_CLAUSES}"
    assert len(set(ids)) == _N_CLAUSES, "存在重复写入的 clause_id"
