"""导入 Phase 2 的进度反馈必须覆盖每个耗时步骤

背景（2026-09-26 实测）：Phase 2 原先只在 60 / 70 / 85 / 100 四档更新进度，
`progress=70`（「正在分类 N 条条文...」）到 `progress=100` 之间**整整 42 秒不更新**
——其中约 29 秒是首次加载 BGE 模型、约 20 秒是向量编码、约 12 秒是写向量索引。
CJJ 2-2008 实测 Phase 2 全程 45.6 秒，进度条有 42 秒静止，用户据此判定「卡死」
并反复重导（真正的导入失败原因是确认接口的 1MB 表单字段上限，见
tests/test_confirm_transport.py —— 但进度静默是让人误判的直接推手）。

本文件锁定机制而非时长：**每个耗时步骤都有自己的进度档与说明性文案**，
进度单调不减，且相邻档之间不出现大跳。
"""
from app.database import init_db
from app.routes import import_routes as ir

# 条文条数取 > 2 个写批（VECTOR_WRITE_BATCH=500），以验证写入进度是分档推进的
_N_CLAUSES = 1200
# 「平滑」的可测代理：相邻两次进度更新不得跳超过这么多
_MAX_JUMP = 20


class _Recorder(dict):
    """记录每次 update 的 (progress, message)；供断言进度序列与文案"""

    def __init__(self, sink):
        super().__init__()
        self._sink = sink

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self._sink.append((self.get("progress"), self.get("message", "")))


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "prog.db"))
    monkeypatch.setattr(ir, "OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(ir, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()


def _many_clause_md(n=_N_CLAUSES):
    return "".join(f"{i + 1}.0.1 本条为进度反馈测试条文内容。\n" for i in range(n))


class _FakeTable:
    def __init__(self, sink):
        self._sink = sink

    def add(self, records):
        self._sink.append(len(records))


class _FakeVS:
    """向量表已存在的替身；不接触真实 LanceDB"""

    def __init__(self, sink):
        self._sink = sink

    def _table_exists(self):
        return True

    def _get_table(self):
        return _FakeTable(self._sink)

    def optimize(self):
        """导入收尾会压实（`VectorStore.optimize`）：替身无真实表可压实，直接成功"""
        return True


def _run_phase2(monkeypatch, tmp_path, task_id):
    """跑一遍 Phase 2，返回记录的 (progress, message) 序列"""
    _setup(monkeypatch, tmp_path)
    sink: list[tuple] = []
    monkeypatch.setattr(ir, "progress_store", {task_id: _Recorder(sink)})
    monkeypatch.setattr(ir, "VectorStore", lambda: _FakeVS([]))
    # 不加载真实 BGE 模型（约 29 秒）：本文件断言的是进度机制，不是模型行为
    monkeypatch.setattr("app.ai.embedding.get_model", lambda: object())
    monkeypatch.setattr("app.ai.embedding.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    ir._process_import_phase2(
        task_id, _many_clause_md(), "进度测试规范", "GB/T 99999-2030",
        str(tmp_path / "f.md"), "hash_prog", "现行", "",
    )
    return sink


def _progresses(sink):
    return [p for p, _ in sink if p is not None]


def _messages(sink):
    return " || ".join(m for _, m in sink)


def test_progress_is_monotonic_and_reaches_100(monkeypatch, tmp_path):
    """进度必须单调不减且终态为 100（回退会让用户以为任务重来）"""
    sink = _run_phase2(monkeypatch, tmp_path, "pg000001")
    vals = _progresses(sink)
    assert vals, "Phase 2 没有任何进度更新"
    assert vals[-1] == 100, f"终态不是 100: {vals[-1]}"
    assert all(b >= a for a, b in zip(vals, vals[1:])), f"进度出现回退: {vals}"


def test_each_long_stage_announces_itself(monkeypatch, tmp_path):
    """三个耗时步骤各自要有说明性文案——否则用户不知道卡在哪一步

    对应实测的三个长停顿：加载 BGE 模型（约 29s）、向量编码（约 20s）、
    写向量索引（约 12s）。缺任何一条，进度条就会在那一档静止几十秒。
    """
    sink = _run_phase2(monkeypatch, tmp_path, "pg000002")
    joined = _messages(sink)
    assert "向量模型" in joined, f"缺少「加载向量模型」的进度提示: {joined}"
    assert "生成向量" in joined, f"缺少「生成向量」的进度提示: {joined}"
    assert "写入向量索引" in joined, f"缺少「写入向量索引」的进度提示: {joined}"


def test_progress_is_granular_not_four_steps(monkeypatch, tmp_path):
    """进度档数要明显多于原先的 4 档（60/70/85/100）"""
    sink = _run_phase2(monkeypatch, tmp_path, "pg000003")
    vals = set(_progresses(sink))
    assert len(vals) >= 6, f"进度档过少（{sorted(vals)}），长阶段仍会静默"


def test_no_step_jumps_too_far(monkeypatch, tmp_path):
    """相邻进度不得大跳（大跳等价于静默等待）"""
    sink = _run_phase2(monkeypatch, tmp_path, "pg000004")
    vals = _progresses(sink)
    jumps = [(a, b) for a, b in zip(vals, vals[1:]) if b - a > _MAX_JUMP]
    assert not jumps, f"存在超过 {_MAX_JUMP} 的进度跳变（等价静默等待）: {jumps}"


def test_vector_write_progress_reports_done_over_total(monkeypatch, tmp_path):
    """写向量索引要报 (已写/总数)，且分批推进——1200 条应出现多档"""
    sink = _run_phase2(monkeypatch, tmp_path, "pg000005")
    write_msgs = [m for _, m in sink if "写入向量索引" in m]
    assert len(write_msgs) >= 2, (
        f"1200 条只报了 {len(write_msgs)} 次写入进度（应随批次推进）: {write_msgs}")
    for m in write_msgs:
        assert f"/{_N_CLAUSES}" in m, f"写入进度未报总数: {m}"
