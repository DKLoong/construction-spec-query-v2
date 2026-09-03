"""system_logs 保留清理（spec §7.3 / D15）

默认保留 90 天：启动时后台清理过期日志；维护界面手动清理（全部 / 90 天前 /
指定分类）。手动清理动作的审计行由路由在清理之后写入（见 log_routes）。
"""
from app.config import LOG_RETENTION_DAYS, LOG_CLEAN_BATCH
from app.database import get_db


def cleanup_expired(conn, days: int = LOG_RETENTION_DAYS,
                    batch: int = LOG_CLEAN_BATCH) -> int:
    """删除 created_at 早于 now-days 的 system_logs，分批防长事务，返回删除条数。

    conn 由调用方传入（启动清理沿用现有连接的同一事务）。
    """
    deleted = 0
    while True:
        rows = conn.execute(
            "SELECT id FROM system_logs "
            "WHERE created_at < datetime('now', 'localtime', ?) LIMIT ?",
            (f"-{days} days", batch),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            break
        conn.execute(
            f"DELETE FROM system_logs WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        deleted += len(ids)
    return deleted


def manual_clean(scope: str, category: str = "") -> int:
    """手动清理 system_logs。

    scope:
      - all      删除全部日志
      - older    删除 90 天前（保留策略，D15）
      - category 删除指定分类
    返回删除条数。scope 非白名单抛 ValueError（路由层已校验，双保险防误删）。
    """
    if scope not in ("all", "older", "category"):
        raise ValueError(f"非法清理范围: {scope}")
    if scope == "category" and not category:
        raise ValueError("category 范围需提供分类")

    with get_db() as conn:
        if scope == "all":
            conn.execute("DELETE FROM system_logs")
            return conn.execute("SELECT changes() AS n").fetchone()["n"]
        if scope == "category":
            cur = conn.execute(
                "DELETE FROM system_logs WHERE category = ?", (category,))
            return cur.rowcount
        return cleanup_expired(conn)
