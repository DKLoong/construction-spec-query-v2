"""结构化行为日志工具（写 system_logs 表）

P1 先建最小版供维护工具（健康检查/导出）记录；P3 日志管理扩展为全量
埋点工具（导入/删除/规则/复核等），本模块签名保持稳定。
"""
import json
import logging

logger = logging.getLogger(__name__)


def json_detail(obj) -> str:
    """把 detail 安全序列化为 JSON 字符串（中文不转义）。

    供各路由埋点时统一调用，避免 detail 传不可序列化对象（set/自定义类/
    异常对象等）在埋点行抛错中断主流程。失败时回退 str(obj)。
    """
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


def log_action(category: str, level: str, action: str,
               detail: str | None = None, username: str | None = None,
               duration_ms: int | None = None) -> None:
    """写入一条 system_logs 记录（表不存在/失败时静默降级，不打断主流程）"""
    try:
        from app.database import get_db
        with get_db() as conn:
            conn.execute(
                """INSERT INTO system_logs (category, level, action, detail, username, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (category, level, action, detail, username, duration_ms),
            )
    except Exception:
        logger.warning("system_logs 写入失败 (category=%s, action=%s)", category, action)
