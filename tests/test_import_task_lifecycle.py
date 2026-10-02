"""导入任务台账（import_tasks 表）的生命周期：时限清理 + 并发取用守卫

两个真实缺陷：

1. **无 TTL，只增不减**：任务进入 done/error 后条目永久驻留（只有取消会删）。
2. **取消与后台线程并发**：`cancel_review` 只在 `status == "done"` 时拒绝取消，
   也就是说**允许取消 processing 中的任务** → 条目被删时 Phase 1/2 线程还在跑，
   它随后的状态写入必须安全地失败，而不是抛异常。这是真实窗口，不是理论风险。

清理口径（用户裁定）：
- done / error 超 `import.task_ttl_terminal_min` 分钟 → 只删台账条目；
- review_needed 超 `import.task_ttl_review_hours` 小时 → 删台账条目**并删磁盘**
  （原文件 + OCR 产物），等于「超过一天没审就算了」；
- 运行中（uploading / processing）一律不碰。
磁盘删除复用 `_cleanup_task_artifacts`，它自带「被 specifications 引用则不删」的保护。
"""
import time

import pytest

from app.routes import import_routes as ir
from tests.conftest import import_task_row


def _age_task(ir, task_id, age_s):
    """把 updated_at 往回拨 age_s 秒（_update_task 会盖时间戳，只能在写入后改）"""
    from app.database import get_db
    with get_db() as conn:
        conn.execute("UPDATE import_tasks SET updated_at = ? WHERE task_id = ?",
                     (time.time() - age_s, task_id))


# ═══════════════════════════════════════════
# _task_disposition：纯函数，决定单条台账条目的去向
# ═══════════════════════════════════════════

TERM = 300.0      # 终态时限 5 分钟（秒）
REVIEW = 3600.0   # 待审查时限 1 小时（秒）


def _task(status, age_s, **extra):
    t = {"status": status, "updated_at": 1000.0 - age_s}
    t.update(extra)
    return t


def test_disposition_keeps_running_tasks():
    """运行中的任务绝不能被清理（清掉会让在跑的线程失去把手）"""
    for st in ("uploading", "processing"):
        assert ir._task_disposition(_task(st, age_s=99999), 1000.0, TERM, REVIEW) == "keep"


def test_disposition_terminal_state_uses_terminal_ttl():
    """done/error：未超时保留，超时只删内存（磁盘留着，已入库规范的源文件还有用）"""
    assert ir._task_disposition(_task("done", age_s=TERM - 1), 1000.0, TERM, REVIEW) == "keep"
    assert ir._task_disposition(_task("done", age_s=TERM + 1), 1000.0, TERM, REVIEW) == "drop_memory"
    assert ir._task_disposition(_task("error", age_s=TERM + 1), 1000.0, TERM, REVIEW) == "drop_memory"


def test_disposition_review_needed_drops_disk_too():
    """review_needed：超时连磁盘一起删（用户裁定「超过一天没审就算了」）"""
    assert ir._task_disposition(_task("review_needed", age_s=REVIEW - 1), 1000.0, TERM, REVIEW) == "keep"
    assert ir._task_disposition(
        _task("review_needed", age_s=REVIEW + 1), 1000.0, TERM, REVIEW) == "drop_memory_and_disk"


def test_disposition_unknown_status_is_kept():
    """状态不认识（含未来新增态）→ 保留：宁可留着也不误删"""
    assert ir._task_disposition(_task("some_new_state", age_s=99999), 1000.0, TERM, REVIEW) == "keep"


def test_disposition_without_timestamp_is_kept():
    """没有 updated_at 就无从计算年龄 → 保留（不删算不出年龄的东西）"""
    assert ir._task_disposition({"status": "done"}, 1000.0, TERM, REVIEW) == "keep"


# ═══════════════════════════════════════════
# 取用守卫：取消后残留线程不得抛 KeyError
# ═══════════════════════════════════════════

def test_get_task_returns_none_for_missing():
    assert ir._get_task("nope") is None


def test_update_task_survives_cancelled_task():
    """取消（删行）之后线程再写台账：不抛异常，返回 False 让调用方收工

    这就是用户报的那条风险：cancel_review 不拒绝 processing 中的任务，
    Phase 1/2 线程随后再写台账时会撞上一条已不存在的条目。
    """
    ir.create_task("t1")
    ir._update_task("t1", status="processing", progress=10)
    assert ir._update_task("t1", progress=20) is True
    assert import_task_row("t1")["progress"] == 20

    ir.delete_task("t1")                                  # 模拟取消/超时清理
    assert ir._update_task("t1", progress=30) is False    # 不得抛异常
    assert ir._get_task("t1") is None


def test_update_task_stamps_updated_at():
    """每次更新都要盖时间戳——清理器全靠它算年龄"""
    ir.create_task("t2")
    before = time.time()
    ir._update_task("t2", progress=50)
    assert import_task_row("t2")["updated_at"] >= before


# ═══════════════════════════════════════════
# sweep_progress_store：整台账清扫
# ═══════════════════════════════════════════

@pytest.fixture
def _paths(tmp_path, monkeypatch):
    up, out = tmp_path / "uploads", tmp_path / "outputs"
    up.mkdir()
    out.mkdir()
    monkeypatch.setattr(ir, "UPLOAD_DIR", str(up))
    monkeypatch.setattr(ir, "OUTPUT_DIR", str(out))
    return up, out


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    """台账已落库：每条用例换一个临时库

    autouse 的理由不只是省参数——本文件所有用例都会碰到台账，忘了加参数就会打到
    **真实** `data/spec_query.db`（conftest 的会话守卫不覆盖主库，属盲区）。
    """
    from app.database import init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "life.db"))
    init_db()
    return tmp_path / "life.db"


def _ttls(monkeypatch, terminal_s=TERM, review_s=REVIEW):
    """把参数注册表读到的值替换成测试值（同时覆盖「sweep 真的读参数」这条接线）

    ⚠ 注意单位：注册表里两档参数分别是**分钟**与**小时**，由
    `_task_ttl_seconds()` 换算成秒。此处必须按同一单位回填，否则测的是
    "参数被读到了没有"之外的另一回事（首版把 review 秒数也除了 60，
    时限被算成 60 小时，用例静默变味）。
    """
    def _fake(key: str) -> int:
        if "terminal" in key:
            return max(1, int(terminal_s // 60))     # 分钟
        return max(1, int(review_s // 3600))         # 小时

    monkeypatch.setattr("app.params.registry.get_param_int", _fake)


def test_sweep_drops_expired_terminal_from_memory_only(monkeypatch, _paths, _db):
    """终态超时：只删台账条目，磁盘产物必须留着"""
    up, out = _paths
    src = up / "aaaabbbb.pdf"
    src.write_bytes(b"x")
    (out / "aaaabbbb").mkdir()
    (out / "aaaabbbb" / "aaaabbbb.md").write_text("x", encoding="utf-8")
    ir.create_task("aaaabbbb")
    ir._update_task("aaaabbbb", status="done")
    _age_task(ir, "aaaabbbb", TERM + 10)

    _ttls(monkeypatch)
    res = ir.sweep_progress_store(now=time.time())

    assert res == {"memory": 1, "disk": 0}
    assert ir._get_task("aaaabbbb") is None
    assert src.exists(), "终态清理不得删磁盘（已入库规范的源文件还要留给审查/追溯）"
    assert (out / "aaaabbbb").is_dir()


def test_sweep_expired_review_deletes_memory_and_disk(monkeypatch, _paths, _db):
    """待审查超时：台账条目与磁盘一起清"""
    up, out = _paths
    src = up / "ccccdddd.pdf"
    src.write_bytes(b"x")
    (out / "ccccdddd").mkdir()
    ir.create_task("ccccdddd")
    ir._update_task("ccccdddd", status="review_needed")
    _age_task(ir, "ccccdddd", REVIEW + 10)

    _ttls(monkeypatch)
    res = ir.sweep_progress_store(now=time.time())

    assert res == {"memory": 1, "disk": 1}
    assert ir._get_task("ccccdddd") is None
    assert not src.exists()
    assert not (out / "ccccdddd").exists()


def test_sweep_skips_running_and_fresh(monkeypatch, _paths, _db):
    """运行中的、以及未超时的，一律不动"""
    for task_id, status, age in (("running1", "processing", 99999),
                                 ("fresh000", "done", 1),
                                 ("review01", "review_needed", 1)):
        ir.create_task(task_id)
        ir._update_task(task_id, status=status)
        _age_task(ir, task_id, age)

    _ttls(monkeypatch)
    res = ir.sweep_progress_store(now=time.time())

    assert res == {"memory": 0, "disk": 0}
    assert {t["task_id"] for t in ir.iter_tasks()} == {"running1", "fresh000", "review01"}


def test_sweep_keeps_file_referenced_by_specifications(monkeypatch, _paths, _db):
    """被 specifications 引用的源文件：台账条目照删，**磁盘文件不删**

    复用 _cleanup_task_artifacts 自带的保护，这条用例把它钉住——
    否则「超时连磁盘一起删」会误删已入库规范的原始文件。
    """
    up, _ = _paths
    src = up / "eeeeffff.pdf"
    src.write_bytes(b"x")
    from app.database import get_db
    with get_db() as conn:
        conn.execute(
            """INSERT INTO specifications (code, title, source_path, output_dir)
               VALUES (?, ?, ?, ?)""",
            ("PROBE 1.0", "已入库规范", str(src), ""),
        )
    ir.create_task("eeeeffff")
    ir._update_task("eeeeffff", status="review_needed")
    _age_task(ir, "eeeeffff", REVIEW + 10)

    _ttls(monkeypatch)
    res = ir.sweep_progress_store(now=time.time())

    assert res["memory"] == 1
    assert ir._get_task("eeeeffff") is None
    assert src.exists(), "被 specifications 引用的源文件被误删了"


def test_sweep_honors_param_ttls(monkeypatch, _paths, _db):
    """时限来自参数（不是写死的常量）：调大时限后同一批条目就不该被清"""
    ir.create_task("gggghhhh")
    ir._update_task("gggghhhh", status="done")
    _age_task(ir, "gggghhhh", TERM + 10)

    _ttls(monkeypatch, terminal_s=99999)      # 把终态时限调到很大
    res = ir.sweep_progress_store(now=time.time())

    assert res == {"memory": 0, "disk": 0}
    assert ir._get_task("gggghhhh") is not None


def test_sweep_drops_row_from_store_not_just_cache(monkeypatch, _paths, _db):
    """终态超时：删的是**库里的行**——另开连接读不到（不是只清了某层缓存）"""
    import sqlite3
    up, _ = _paths
    src = up / "iiiijjjj.pdf"
    src.write_bytes(b"x")
    ir.create_task("iiiijjjj")
    ir._update_task("iiiijjjj", status="done")
    _age_task(ir, "iiiijjjj", TERM + 10)

    _ttls(monkeypatch)
    assert ir.sweep_progress_store(now=time.time()) == {"memory": 1, "disk": 0}

    conn = sqlite3.connect(str(_db))
    row = conn.execute("SELECT 1 FROM import_tasks WHERE task_id = ?",
                       ("iiiijjjj",)).fetchone()
    conn.close()
    assert row is None, "行还在库里 → 说明只清了缓存层"
    assert src.exists(), "终态清理不得删磁盘"
