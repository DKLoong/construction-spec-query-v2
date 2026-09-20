"""清理同 (维度, 关键词) 的重复规则，并补建唯一索引（一次性数据迁移；幂等；可回滚）

**批准依据**：2026-09-20 用户裁决「**保留有标签的那条，删除其余无标签的**」。
库内当时仅 1 组重复：dim6 的 `翻模` 共 4 行（843/844/845 无标签 + 846 标签=翻模），
系测试期间反复新建所致，四行 hit/confirmed 均为 0，故删除不丢任何统计。

**为什么以 (dimension, pattern) 为规则身份**（依据是代码机制，不是约定）：
- `rule_engine.classify_clause` 同维**只取最高分那一条**（`if score > scores[dim]`）
  → 同维同词的第二条永远是死配置，不会生效；
- `rule_sink.bump_rule` 按 `WHERE dimension=? AND pattern=?` 定位规则累加命中
  → 重复时命中统计落到**不确定的那一行**，而规则自动启停正是按
  `confirmed / hit_count` 正确率算的，会被污染；
- `scripts/seed_rules.py` 的幂等键原本是 (dim, sub_field, pattern)，与前者不一致，
  已一并对齐。
- 跨维度同词**合法**（`钢筋` 在 dim5/dim6 含义不同），故本迁移绝不跨维度合并。

**保留优先级**（通用规则，便于日后复用）：有标签 > (hit_count + confirmed) 高 > id 小。
被删行的 hit/confirmed 会**累加进保留行**，避免丢失真实命中历史（否则保留行可能因
hit_count=0 被质量报表误判为僵尸规则）。

**安全带**：执行前自动复制一份库到 `<db>.bak-<时间戳>`；全过程单事务，异常即回滚。

用法：
  # 只报告，不改动（默认）
  PYTHONUTF8=1 D:/Python/python.exe scripts/migrate_rule_unique_key.py
  # 执行清理并补建唯一索引
  PYTHONUTF8=1 D:/Python/python.exe scripts/migrate_rule_unique_key.py --apply
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_DB = "data/spec_query.db"
INDEX_NAME = "uq_rule_dim_pattern"


def _score(row: sqlite3.Row | dict) -> tuple:
    """保留优先级排序键（越小越该保留）：无标签排后、统计少排后、id 大排后"""
    has_label = 0 if (row["label"] or "").strip() else 1
    hits = -(int(row["hit_count"] or 0) + int(row["confirmed"] or 0))
    return (has_label, hits, int(row["id"]))


def find_duplicate_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    """返回同 (dimension, pattern) 的重复组，每组内已按保留优先级排好序"""
    conn.row_factory = sqlite3.Row
    keys = conn.execute(
        "SELECT dimension, pattern FROM classification_rules "
        "GROUP BY dimension, pattern HAVING COUNT(*) > 1"
    ).fetchall()
    groups = []
    for k in keys:
        rows = conn.execute(
            "SELECT * FROM classification_rules WHERE dimension = ? AND pattern = ?",
            (k["dimension"], k["pattern"]),
        ).fetchall()
        groups.append(sorted(rows, key=_score))
    return groups


def resolve_duplicates(conn: sqlite3.Connection, apply: bool = False) -> dict:
    """清理重复组。apply=False 时只报告。返回处理明细。"""
    groups = find_duplicate_groups(conn)
    report = {"groups": [], "deleted": 0, "merged": 0, "index": "未尝试"}
    for rows in groups:
        keeper, losers = rows[0], rows[1:]
        merged_hits = sum(int(r["hit_count"] or 0) for r in losers)
        merged_conf = sum(int(r["confirmed"] or 0) for r in losers)
        report["groups"].append({
            "dimension": keeper["dimension"],
            "pattern": keeper["pattern"],
            "keep_id": keeper["id"],
            "keep_label": keeper["label"],
            "drop_ids": [r["id"] for r in losers],
            "drop_labels": [r["label"] for r in losers],
            "merged_hits": merged_hits,
            "merged_confirmed": merged_conf,
        })
        if apply:
            conn.execute(
                "UPDATE classification_rules SET hit_count = ?, confirmed = ?, "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (int(keeper["hit_count"] or 0) + merged_hits,
                 int(keeper["confirmed"] or 0) + merged_conf, keeper["id"]),
            )
            conn.executemany(
                "DELETE FROM classification_rules WHERE id = ?",
                [(r["id"],) for r in losers],
            )
            report["deleted"] += len(losers)
            report["merged"] += 1

    if apply:
        # 清理完再补建唯一索引（幂等）。建不起来说明仍有残留重复，如实报出而非吞掉。
        try:
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
                "ON classification_rules(dimension, pattern)"
            )
            report["index"] = "已建/已存在"
        except sqlite3.IntegrityError:
            report["index"] = "仍失败：库内还有重复，请再跑一次检查"
    return report


def _print_report(report: dict, apply: bool) -> None:
    if not report["groups"]:
        print("没有同 (维度, 关键词) 的重复规则，无需清理。")
    for g in report["groups"]:
        print(f"  [{g['dimension']} · {g['pattern']}] 保留 id={g['keep_id']}"
              f"（标签={g['keep_label'] or '空'}）；"
              f"删除 {g['drop_ids']}（标签={g['drop_labels']}）"
              f"；并入命中 {g['merged_hits']}/确认 {g['merged_confirmed']}")
    if apply:
        print(f"\n已删除 {report['deleted']} 行，合并 {report['merged']} 组；"
              f"唯一索引 {INDEX_NAME}：{report['index']}")
    else:
        print("\n[仅报告] 未改动数据。加 --apply 执行清理并补建唯一索引。")


def main() -> int:
    ap = argparse.ArgumentParser(description="清理重复规则并补建唯一索引")
    ap.add_argument("--db", default=None, help=f"库路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只报告）")
    args = ap.parse_args()

    db = Path(args.db) if args.db else Path(DEFAULT_DB)
    if not db.is_absolute():
        db = Path(__file__).resolve().parents[1] / db
    if not db.exists():
        print(f"库不存在：{db}")
        return 1

    print(f"库：{db}")
    if args.apply:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db.with_suffix(db.suffix + f".bak-{stamp}")
        shutil.copy2(db, backup)
        print(f"已备份：{backup}")

    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        report = resolve_duplicates(conn, apply=args.apply)
        if args.apply:
            conn.commit()          # 单事务：上面任一步异常都会走到除外的回滚
        _print_report(report, args.apply)
        return 0
    except Exception:
        if args.apply:
            conn.rollback()
            print("执行失败，已回滚；备份文件仍在，可人工核查。")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
