"""模块级 logger → system_logs 落表测试（app/logging_setup.py）

背景：app/main.py 原先没有任何 logging 配置，模块级 logger.warning/error 只能靠
Python 的 lastResort 兜底写 stderr——没有时间戳、不进数据库，因此**在产品自带的
日志管理 UI 里 100% 不可见**（全项目 47 处告警集体失声）。词库「整表失效」告警
就是这样静默了 13 天。本模块把 WARNING+ 的 app.* 日志落进 system_logs。
"""
import logging
import queue
import sqlite3
import time

import pytest


def _wait_rows(tmp_db, timeout=3.0):
    """轮询等待写线程落表（异步落表，测试需等）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = sqlite3.connect(tmp_db)
        n = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
        conn.close()
        if n:
            return n
        time.sleep(0.02)
    return 0


@pytest.fixture
def log_env(monkeypatch, tmp_path):
    """临时库 + 干净的 logging_setup 生命周期"""
    import app.logging_setup as ls
    from app.database import init_db

    db = tmp_path / "log.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db))
    init_db()
    ls.setup_logging()
    try:
        yield ls, str(db)
    finally:
        ls.shutdown_logging()


def test_startup_installs_logging(monkeypatch):
    """app.startup() 必须安装日志桥（否则模块级告警继续对日志界面不可见）"""
    import app.main as m

    called = []
    monkeypatch.setattr(m, "setup_logging", lambda: called.append(1))
    for name in ("init_db", "_startup_log_cleanup", "_startup_vector_sync",
                 "_startup_warmup_embedding", "_startup_fts_optimize"):
        monkeypatch.setattr(m, name, lambda: None)

    m.startup()

    assert called == [1], "startup() 应调用 setup_logging()"


def test_logger_to_category_mapping():
    """logger 名 → category 映射规则（表驱动）"""
    from app.logging_setup import logger_to_category

    cases = {
        "app.lexicon.store": "lexicon",
        "app.lexicon.expand": "lexicon",
        "app.ocr.paddle_api": "ocr",
        "app.search.vector_search": "search",
        "app.ai.cli_client": "ai",
        "app.classifier.rule_engine": "classify",
        "app.routes.lexicon_routes": "lexicon",
        "app.routes.rules_routes": "rules",
        "app.main": "app",
        "app": "app",
    }
    for name, expected in cases.items():
        assert logger_to_category(name) == expected, name


def test_warning_is_persisted_to_system_logs(log_env):
    """WARNING 应落 system_logs：category/level/action/detail 均正确"""
    ls, db = log_env
    logging.getLogger("app.lexicon.store").warning(
        "词库 equiv 词条冲突（同一词属多组），本次加载作废: %s", "混凝土浇筑")

    assert _wait_rows(db) >= 1
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT category, level, action, detail FROM system_logs ORDER BY id DESC"
    ).fetchone()
    conn.close()
    assert row[0] == "lexicon"
    assert row[1] == "WARNING"
    assert "混凝土浇筑" in row[2]
    assert "app.lexicon.store" in row[3]


def test_error_is_persisted(log_env):
    """ERROR 同样落表，级别保留"""
    ls, db = log_env
    logging.getLogger("app.search.rerank").error("CrossEncoder 精排异常: %s", "boom")

    assert _wait_rows(db) >= 1
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT category, level FROM system_logs ORDER BY id DESC").fetchone()
    conn.close()
    assert row == ("search", "ERROR")


def test_info_and_debug_are_not_persisted(log_env):
    """INFO/DEBUG 不落表（避免灌表）"""
    ls, db = log_env
    lg = logging.getLogger("app.lexicon.store")
    lg.info("这条不该落表")
    lg.debug("这条也不该落表")
    time.sleep(0.4)
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
    conn.close()
    assert n == 0


def test_non_app_logger_is_ignored(log_env):
    """第三方/uvicorn logger 不落表（避免访问日志灌表）"""
    ls, db = log_env
    logging.getLogger("uvicorn.error").warning("第三方告警不该落表")
    logging.getLogger("jieba").warning("第三方告警不该落表")
    time.sleep(0.4)
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
    conn.close()
    assert n == 0


def test_queue_full_does_not_block(monkeypatch):
    """队列满时 emit 不得阻塞、不得抛（宁可丢日志也不能拖慢请求）"""
    import app.logging_setup as ls

    class _FullQueue:
        def put_nowait(self, item):
            raise queue.Full

    monkeypatch.setattr(ls, "_queue", _FullQueue())
    handler = ls.SystemLogsHandler()
    record = logging.LogRecord("app.x", logging.WARNING, "f.py", 1, "msg", None, None)
    handler.emit(record)  # 不抛、不阻塞即通过


def test_writer_failure_does_not_recurse_or_raise(monkeypatch, capsys, tmp_path):
    """写库失败必须只降级到 stderr，绝不调用 logger（否则无限递归）"""
    import app.logging_setup as ls

    # 指向不存在的目录 → 连接失败
    monkeypatch.setattr("app.database.DATABASE_PATH",
                        str(tmp_path / "nope" / "x.db"))
    item = ls._make_item(logging.LogRecord(
        "app.x", logging.ERROR, "f.py", 1, "写不进去", None, None))

    ls._write_row(item)  # 不抛即通过

    captured = capsys.readouterr()
    assert "system_logs" in captured.err or "写入失败" in captured.err


def test_shutdown_stops_writer_thread(log_env):
    """shutdown_logging() 后写线程必须已退出（规则 1.2：异步任务须有退出条件）"""
    ls, _ = log_env
    assert ls._writer_alive() is True
    ls.shutdown_logging()
    assert ls._writer_alive() is False


def test_setup_is_idempotent(log_env):
    """重复 setup 不得产生第二个写线程/重复 handler（--reload 会重复调用）"""
    ls, _ = log_env
    ls.setup_logging()
    ls.setup_logging()
    assert ls._writer_count() == 1


def test_flush_waits_for_pending_records(log_env):
    """flush() 应等到队列排空（供测试与关停使用）"""
    ls, db = log_env
    for i in range(5):
        logging.getLogger("app.ocr.paddle_api").warning("告警 %d", i)
    assert ls.flush(timeout=3.0) is True
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
    conn.close()
    assert n == 5
