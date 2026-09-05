"""存量迁移打底：规则表 label/pattern → term_labels 权威词典 + 停用碎片。

聚合源：classification_rules 中 dim4/5/6、label 非空、(locked=1 OR confirmed>0)。
碎片停用：confirmed=0 且 label 不在新词典 → is_active=0（保留供审计）。
幂等：INSERT OR IGNORE 走 (dimension,label) 唯一键，可重复跑。
用法：D:/Python/python.exe scripts/migrate_term_dict.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db
from app.termdict import invalidate_term_cache, upsert_term_label


def run_migration() -> dict:
    """执行迁移，返回 {inserted, skipped_conflict, disabled}。"""
    stats = {"inserted": 0, "skipped_conflict": 0, "disabled": 0}
    with get_db() as conn:
        rows = conn.execute(
            """SELECT dimension, label, pattern, hit_count
               FROM classification_rules
               WHERE dimension IN ('dim4','dim5','dim6')
                 AND label IS NOT NULL AND label != ''
                 AND (locked = 1 OR confirmed > 0)
               ORDER BY hit_count DESC"""
        ).fetchall()
        picked: dict[tuple[str, str], str] = {}
        for r in rows:
            key = (r["dimension"], r["label"])
            pattern = (r["pattern"] or "").strip()
            if not pattern:
                continue
            picked.setdefault(key, pattern)   # hit 最高者先到（ORDER BY hit DESC）
        for (dim, label), canonical in picked.items():
            try:
                upsert_term_label(conn, dim, label, canonical=canonical, source="migrate")
                stats["inserted"] += 1
            except Exception as e:            # 保底：冲突行跳过不阻断整体迁移
                print(f"[migrate] 跳过 (dim,label)=({dim},{label}) 冲突: {e}")
                stats["skipped_conflict"] += 1
        # 停用碎片：confirmed=0 且其 label 未入新词典
        cur = conn.execute(
            """UPDATE classification_rules SET is_active = 0,
                 updated_at = datetime('now','localtime')
               WHERE dimension IN ('dim4','dim5','dim6')
                 AND confirmed = 0
                 AND label IS NOT NULL AND label != ''
                 AND NOT EXISTS (
                     SELECT 1 FROM term_labels t
                     WHERE t.dimension = classification_rules.dimension
                       AND t.label = classification_rules.label)"""
        )
        stats["disabled"] = cur.rowcount
    invalidate_term_cache()
    return stats


def main() -> None:
    s = run_migration()
    print(f"入典 {s['inserted']} 个权威标签；停用碎片规则 {s['disabled']} 条；"
          f"跳过冲突 {s['skipped_conflict']} 处。")


if __name__ == "__main__":
    main()
