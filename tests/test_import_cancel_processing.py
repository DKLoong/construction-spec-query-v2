"""取消 processing 中的导入任务：不得撞写锁报 500，Phase 2 必须真停并收尾

背景（终审 F1）：`_process_import_phase2` 从 INSERT specifications/clauses 到 commit
一直持有未提交写事务（实测约 10 秒）。旧 `cancel_review` 对 processing 态先删磁盘、
再 `delete_task`（另开连接写库）→ 撞写锁 → busy_timeout=5000 到期抛
`sqlite3.OperationalError: database is locked` → 端点 500；而 Phase 2 的向量循环取消
守卫读的是已提交快照，看不到行被删 → 永不触发 → 照常 commit，但源文件与 OCR 目录
已被删。

修法：取消对 processing 态只发**进程内信号**（`mark_cancelled`），由 Phase 2 在下一个
检查点收工、rollback（锁释放）后再删行 + 清磁盘。本文件用「阻塞在 threading.Event 上
的向量 add() 替身」把 Phase 2 确定性地停在事务中途，不靠 sleep 赌时序。
"""
import asyncio
import threading
from pathlib import Path

from starlette.requests import Request

from app.database import get_db, init_db
from app.routes import import_routes as ir
from tests.conftest import seed_import_task


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "cancel.db"))
    monkeypatch.setattr(ir, "OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(ir, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()


def _md(n: int = 8) -> str:
    return "".join(f"{i + 1}.0.1 本条为取消测试条文内容。\n" for i in range(n))


def _cancel_request() -> Request:
    """cancel_review 只读 request.state.username —— 绕过 AuthMiddleware 的最小替身"""
    req = Request({"type": "http", "method": "POST",
                   "path": "/import/review/x/cancel", "headers": []})
    req.state.username = "admin"
    return req


class _BlockingTable:
    """add() 阻塞在 release 上：把 Phase 2 确定性地停在向量写入检查点（事务中途）"""

    def __init__(self, entered: threading.Event, release: threading.Event):
        self._entered = entered
        self._release = release

    def add(self, records):
        self._entered.set()
        if not self._release.wait(timeout=30):
            raise TimeoutError("Phase 2 未被测试放行")


class _BlockingVS:
    """向量表已存在的替身；不接触真实 LanceDB"""

    def __init__(self, entered: threading.Event, release: threading.Event):
        self._entered = entered
        self._release = release

    def _table_exists(self):
        return True

    def _get_table(self):
        return _BlockingTable(self._entered, self._release)

    def optimize(self):
        return True


def test_cancel_processing_signals_and_phase2_stops(monkeypatch, tmp_path):
    """(a)(b)(c)：processing 中途取消不再 500，Phase 2 真停、不 commit，行与磁盘被清"""
    _setup(monkeypatch, tmp_path)
    task_id = "cc000001"
    seed_import_task(task_id, status="processing", progress=55)

    upload = Path(ir.UPLOAD_DIR)
    upload.mkdir(parents=True, exist_ok=True)
    (upload / f"{task_id}.pdf").write_bytes(b"%PDF-fake")
    ocr_dir = Path(ir.OUTPUT_DIR) / task_id / "imgs"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    (ocr_dir / "a.jpg").write_bytes(b"img-a")

    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(ir, "VectorStore", lambda: _BlockingVS(entered, release))
    monkeypatch.setattr("app.ai.embedding.get_model", lambda: object())
    monkeypatch.setattr("app.ai.embedding.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])

    errors: list[Exception] = []

    def _run():
        try:
            ir._process_import_phase2(
                task_id, _md(), "取消测试规范", "GB/T 77777-2030",
                str(tmp_path / "f.md"), "hash_cancel", "现行", "",
            )
        except Exception as e:  # 后台异常收集到断言里报，避免线程里静默吞掉
            errors.append(e)

    t = threading.Thread(target=_run)
    t.start()
    try:
        assert entered.wait(timeout=30), "Phase 2 未进入向量写入检查点"
        # 此刻 Phase 2 已 INSERT specifications/clauses、未 commit，正持未提交写事务
        resp = asyncio.run(ir.cancel_review(_cancel_request(), task_id))
        assert resp.status_code == 200, f"取消返回非 200: {resp.status_code}"
        assert resp.headers.get("HX-Redirect") == "/"
    finally:
        release.set()
        t.join(timeout=30)

    assert not t.is_alive(), "Phase 2 线程在取消后未结束"
    assert not errors, f"Phase 2 抛出异常: {errors}"

    # (b) 未 commit 出 specifications/clauses 行
    with get_db() as conn:
        n_spec = conn.execute(
            "SELECT COUNT(*) FROM specifications WHERE title = ?", ("取消测试规范",)
        ).fetchone()[0]
        n_clause = conn.execute(
            """SELECT COUNT(*) FROM clauses c
               JOIN specifications s ON c.spec_id = s.id
               WHERE s.title = ?""", ("取消测试规范",)
        ).fetchone()[0]
    assert n_spec == 0, f"取消后仍 commit 出 {n_spec} 行 specifications"
    assert n_clause == 0, f"取消后仍 commit 出 {n_clause} 行 clauses"

    # (c) 台账行与磁盘产物被清理（不留「僵尸 processing 行」）
    assert ir._get_task(task_id) is None, "取消后仍留下台账行（僵尸 processing）"
    assert not (upload / f"{task_id}.pdf").exists(), "上传源文件未被清理"
    assert not (Path(ir.OUTPUT_DIR) / task_id).exists(), "OCR 目录未被清理"


def test_cancel_during_ocr_phase1_does_not_resurrect(monkeypatch, tmp_path):
    """Phase 1（OCR 阶段）被取消：OCR 返回后不得写回 review_needed 复活，直接收尾"""
    _setup(monkeypatch, tmp_path)
    task_id = "cc000003"
    seed_import_task(task_id, status="processing", progress=20)

    upload = Path(ir.UPLOAD_DIR)
    upload.mkdir(parents=True, exist_ok=True)
    pdf = upload / f"{task_id}.pdf"
    pdf.write_bytes(b"%PDF-fake")

    monkeypatch.setattr(ir, "is_scanned", lambda path: False)
    monkeypatch.setattr(ir, "extract_text", lambda path: "1.0.1 OCR 产物。\n")

    # 模拟「OCR 进行中用户点了取消」：信号已登记，OCR 返回后必须收尾而非写 review_needed
    ir.mark_cancelled(task_id)
    ir._process_import(task_id, str(pdf), "OCR 取消规范", "GB/T 66666-2030", "hash_ocr")

    assert ir._get_task(task_id) is None, "被取消的任务在 OCR 后复活成待审查/其它状态"
    assert not pdf.exists(), "上传源文件未被清理"
    assert not (Path(ir.OUTPUT_DIR) / task_id).exists()


def test_cancel_review_needed_still_sync_deletes(monkeypatch, tmp_path):
    """(d) 非 processing 态取消保持原路径：删磁盘 + 删行 + HX-Redirect"""
    _setup(monkeypatch, tmp_path)
    task_id = "cc000002"
    seed_import_task(task_id, status="review_needed")

    upload = Path(ir.UPLOAD_DIR)
    upload.mkdir(parents=True, exist_ok=True)
    (upload / f"{task_id}.pdf").write_bytes(b"%PDF-fake")
    ocr_dir = Path(ir.OUTPUT_DIR) / task_id
    ocr_dir.mkdir(parents=True, exist_ok=True)

    resp = asyncio.run(ir.cancel_review(_cancel_request(), task_id))

    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/"
    assert ir._get_task(task_id) is None
    assert not (upload / f"{task_id}.pdf").exists()
    assert not ocr_dir.exists()
