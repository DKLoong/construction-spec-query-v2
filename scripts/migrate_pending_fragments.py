"""存量碎片规则 → 规则级 pending 一次性迁移（幂等，可重跑）

背景：AI 自动采纳（auto_adopted）曾把 `extract_keywords` 提取的通用/HTML 残留词直接
沉淀为 enabled 规则（confirmed=0 碎片），命中即打错误标签。本脚本把 dim4/5/6 下
`confirmed=0 且 is_active=1 且 label 非空` 的碎片规则导入为「规则级 pending」
（clause_id=NULL，无来源条文），进入词面校核 Tab2 逐条裁决；驳回会联动停用碎片规则。

规则：
- locked=1 的预置种子不算碎片，跳过（skipped_seed_locked）
- pattern strip 为空跳过
- 同键 (dimension, pattern, label) 已有规则级 pending/approved/rejected 行跳过
  （skipped_duplicate，幂等）
- 不改任何 classification_rules 状态（启用/停用由审核裁决触发）

可作为 `from scripts.migrate_pending_fragments import run_migration` 供测试。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db, init_db
from app.classifier import rule_pending


def run_migration(conn) -> dict:
    """遍历存量碎片规则，导入为规则级 pending。返回 {imported, skipped_duplicate, skipped_seed_locked}。"""
    stats = {"imported": 0, "skipped_duplicate": 0, "skipped_seed_locked": 0}
    rows = conn.execute(
        "SELECT dimension, pattern, label, locked FROM classification_rules "
        "WHERE dimension IN ('dim4', 'dim5', 'dim6') AND confirmed=0 AND is_active=1 "
        "AND label IS NOT NULL AND label != ''"
    ).fetchall()
    for r in rows:
        if r["locked"]:
            stats["skipped_seed_locked"] += 1
            continue
        pattern = (r["pattern"] or "").strip()
        if not pattern:
            continue
        # 同键已有规则级 pending/approved/rejected 行 → 跳过（幂等）
        exists = conn.execute(
            "SELECT 1 FROM rule_pending WHERE dimension=? AND pattern=? AND label=? "
            "AND clause_id IS NULL LIMIT 1",
            (r["dimension"], pattern, r["label"])).fetchone()
        if exists:
            stats["skipped_duplicate"] += 1
            continue
        if rule_pending.insert_pending(conn, None, r["dimension"], pattern, r["label"]) is not None:
            stats["imported"] += 1
    return stats


def main():
    init_db()  # 确保 schema（含规则级 pending 部分唯一索引）就绪
    with get_db() as conn:
        stats = run_migration(conn)
    print(
        f"导入 {stats['imported']} 条规则级 pending，"
        f"重复跳过 {stats['skipped_duplicate']}，"
        f"种子锁定跳过 {stats['skipped_seed_locked']}"
    )
    print("完成。碎片规则启用/停用由词面校核裁决触发，本脚本未改任何规则状态。")


if __name__ == "__main__":
    main()
