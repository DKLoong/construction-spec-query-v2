"""把模块级 logger(WARNING+) 落进 system_logs —— 接通应用里原本互不相连的两套日志

**背景**：`app/main.py` 原先没有任何 logging 配置（无 handler / 无文件 / 无轮转），
模块级 `logger.warning/error` 只能靠 Python 的 `lastResort` 兜底写 stderr——没有
时间戳、不轮转、不进数据库。而产品自带的日志管理 UI **只读 `system_logs` 表**，
那张表只由手工调用的 `log_action()` 写入。于是**任何模块级 logger 输出在产品日志
界面里 100% 不可见**：全项目 47 处告警集体失声，词库「整表失效」告警因此静默了
13 天无人发现。

**设计要点（每条都对应一个已识别的风险）**：

1. **不直接写库，走有界队列 + 单写线程**。`get_db()` 每次开新连接且
   `busy_timeout=5000`；若请求正持写事务，同步写会等满 5 秒才失败——即**每次告警
   给请求加最多 5s 延迟**且日志丢失。异步化后请求路径零阻塞，队列满则丢弃。
2. **递归防护**：handler 与写线程内部**绝不调用 `logger.*`**。否则
   `logging_util.log_action` 的失败分支（其自身调 `logger.warning`）会造成无限
   递归。写库失败只降级写 `sys.stderr`。
3. **只落 `app.*` logger**：避免 uvicorn 访问日志与第三方库告警灌表。
4. **保留 stderr 输出**：同时加带格式的 StreamHandler，`_dev_srv.log` 仍可读，
   不因新增 handler 而让开发者失去终端可见性。
5. 根 logger 的 **level 不动**（保持默认 WARNING），INFO 行为与改动前一致。

用法：`app/main.py` 的 `startup()` 里调用 `setup_logging()`（早于 `init_db()`）。
"""
import logging
import queue
import sqlite3
import sys
import threading
import time

from app.logging_util import json_detail

_HANDLER_FLAG = "_system_logs_handler"
_STREAM_FLAG = "_app_stream_handler"
_WRITER_NAME = "system-logs-writer"

# logger 名前缀 → category（与 system_logs 既有 category 取值风格一致）
_CATEGORY_PREFIXES = (
    ("app.lexicon.", "lexicon"),
    ("app.ocr.", "ocr"),
    ("app.search.", "search"),
    ("app.ai.", "ai"),
    ("app.classifier.", "classify"),
)

_lock = threading.Lock()
_queue: queue.Queue | None = None
_writer: threading.Thread | None = None
_stop = threading.Event()
_dropped = 0


def logger_to_category(name: str) -> str:
    """logger 名 → system_logs.category。

    `app.routes.lexicon_routes` → `lexicon`（去掉 `_routes` 后缀）；
    未匹配的 app.* 归到 `app`。
    """
    n = name or ""
    for prefix, category in _CATEGORY_PREFIXES:
        if n.startswith(prefix):
            return category
    if n.startswith("app.routes."):
        rest = n[len("app.routes."):]
        return rest[:-len("_routes")] if rest.endswith("_routes") else rest
    return "app"


def _make_item(record: logging.LogRecord) -> dict:
    """把 LogRecord 归一为 system_logs 一行（action 存消息，detail 存结构化上下文）"""
    message = record.getMessage()
    return {
        "category": logger_to_category(record.name),
        "level": record.levelname,
        "action": message[:200],
        "detail": json_detail({
            "logger": record.name,
            "pathname": record.pathname,
            "lineno": record.lineno,
            "funcName": record.funcName,
            "message": message,
        }),
    }


def _note_dropped() -> None:
    """队列满时计数并（首次/每千条）提示一次。只写 stderr，绝不走 logging。"""
    global _dropped
    _dropped += 1
    if _dropped == 1 or _dropped % 1000 == 0:
        try:
            sys.stderr.write(f"[logging_setup] 日志队列已满，已丢弃 {_dropped} 条\n")
        except Exception:
            pass


def _write_row(item: dict) -> None:
    """写一条 system_logs。**绝不调用 logger**（防递归）；失败只写 stderr。

    独立短超时连接（timeout=1 / busy_timeout=1000ms）：即便撞上写锁也只等 1 秒，
    且发生在后台写线程里，不影响任何请求。
    """
    conn = None
    try:
        from app.database import DATABASE_PATH
        conn = sqlite3.connect(DATABASE_PATH, timeout=1)
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(
            "INSERT INTO system_logs (category, level, action, detail) VALUES (?, ?, ?, ?)",
            (item["category"], item["level"], item["action"], item["detail"]),
        )
        conn.commit()
    except Exception as e:
        try:
            sys.stderr.write(f"[logging_setup] system_logs 写入失败: {e}\n")
        except Exception:
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _writer_loop(q: queue.Queue) -> None:
    """单写线程：带超时的取队列循环 + `_stop` 退出条件（规则 1.2）。

    队列在启动时以参数传入，避免运行期被替换导致线程取错对象。
    """
    while not _stop.is_set():
        try:
            item = q.get(timeout=1)
        except queue.Empty:
            continue
        try:
            _write_row(item)
        finally:
            q.task_done()


class SystemLogsHandler(logging.Handler):
    """把 `app.*` 的 WARNING+ 记录**非阻塞**投递到有界队列。

    emit 内不做 IO、不调用 logger，符合 logging 的 handler 契约。
    """

    def __init__(self, level: int = logging.WARNING):
        super().__init__(level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not record.name.startswith("app."):
                return
            if record.levelno < logging.WARNING:
                return
            q = _queue
            if q is None:
                return
            try:
                q.put_nowait(_make_item(record))
            except queue.Full:
                _note_dropped()
        except Exception:
            # logging 约定：emit 不得向上抛异常
            self.handleError(record)


def setup_logging(queue_size: int = 1000) -> None:
    """幂等安装：stderr handler + SystemLogsHandler + 单个写线程。

    `--reload` 会重复调用，故必须幂等（重复调用不叠加 handler/线程）。
    """
    global _queue, _writer
    with _lock:
        root = logging.getLogger()
        if getattr(root, _HANDLER_FLAG, None) is None:
            stream = logging.StreamHandler(sys.stderr)
            stream.setLevel(logging.WARNING)
            stream.setFormatter(logging.Formatter(
                "%(levelname)s [%(name)s] %(message)s"))
            setattr(stream, _STREAM_FLAG, True)
            root.addHandler(stream)

            handler = SystemLogsHandler()
            setattr(handler, _HANDLER_FLAG, True)
            root.addHandler(handler)
            setattr(root, _HANDLER_FLAG, True)

        if _writer is None or not _writer.is_alive():
            _stop.clear()
            _queue = queue.Queue(maxsize=queue_size)
            _writer = threading.Thread(target=_writer_loop, args=(_queue,),
                                       name=_WRITER_NAME, daemon=True)
            _writer.start()


def shutdown_logging(timeout: float = 2.0) -> None:
    """停写线程并移除 handler（供测试与优雅关停使用）。幂等。"""
    global _queue, _writer
    with _lock:
        _stop.set()
        writer = _writer
        if writer is not None and writer.is_alive():
            writer.join(timeout=timeout)
        _writer = None

        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, _HANDLER_FLAG, None) or getattr(h, _STREAM_FLAG, None):
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        if getattr(root, _HANDLER_FLAG, None):
            delattr(root, _HANDLER_FLAG)
        _queue = None


def flush(timeout: float = 2.0) -> bool:
    """等待队列排空；返回是否排空。无队列时视为已空。"""
    q = _queue
    if q is None:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if q.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return q.unfinished_tasks == 0


def _writer_alive() -> bool:
    """写线程是否存活（测试用）"""
    w = _writer
    return bool(w is not None and w.is_alive())


def _writer_count() -> int:
    """存活的写线程数（测试用：验证 setup 幂等）"""
    return sum(1 for t in threading.enumerate()
               if t.name == _WRITER_NAME and t.is_alive())
