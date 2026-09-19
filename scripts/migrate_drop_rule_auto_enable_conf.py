"""一次性迁移：移除已废弃参数 classify.rule_auto_enable_conf（幂等，可重跑）

背景：该参数原用于「人工确认时按来源 AI 置信度决定新沉淀的规则是否直接启用」。
2026-09-19 的「打标-沉淀解耦」把词面沉淀收敛到 Tab2、并经
`rule_pending._confirm_clause` 统一写列写规则，`process_feedback` 不再按置信度
启停规则，该参数失去唯一消费方，已从 `app/params/registry.py` 与
`app/config.py` 移除（settings 页不再展示）。

本脚本清理两处历史残留：
- `settings`：该键的参数覆盖值（apply 方案时会写入）
- `param_profiles`：各方案 `values_json` 中的该键

不清理的后果：`param_routes._validate_values` 对未注册键会
`raise ValueError("未知参数: …")`，用户再编辑含该键的方案会直接报错。

可作为 `from scripts.migrate_drop_rule_auto_enable_conf import run_migration` 供测试。

用法：D:/Python/python.exe scripts/migrate_drop_rule_auto_enable_conf.py
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEAD_KEY = "classify.rule_auto_enable_conf"


def run_migration(db_path: str | None = None) -> dict:
    """清理该参数的历史残留，返回 {"settings": n, "profiles": m}（幂等）。"""
    if db_path is None:
        from app.database import DATABASE_PATH as db_path  # 运行时读取，便于测试替换

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:  # 事务：两步清理全成或全不成
            n_settings = conn.execute(
                "DELETE FROM settings WHERE key = ?", (DEAD_KEY,)).rowcount

            n_profiles = 0
            for row in conn.execute(
                    "SELECT id, values_json FROM param_profiles").fetchall():
                values = json.loads(row["values_json"] or "{}")
                if DEAD_KEY in values:
                    values.pop(DEAD_KEY)
                    conn.execute(
                        "UPDATE param_profiles SET values_json = ?, "
                        "updated_at = datetime('now','localtime') WHERE id = ?",
                        (json.dumps(values, ensure_ascii=False), row["id"]))
                    n_profiles += 1
        return {"settings": n_settings, "profiles": n_profiles}
    finally:
        conn.close()


def main() -> None:
    stats = run_migration()
    print(f"[migrate] 已移除 {DEAD_KEY}："
          f"settings 删除 {stats['settings']} 行，"
          f"param_profiles 清理 {stats['profiles']} 个方案")


if __name__ == "__main__":
    main()
