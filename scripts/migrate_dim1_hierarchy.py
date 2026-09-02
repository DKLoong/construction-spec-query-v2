"""规范 dim1 层级/行业迁移：把历史混入 dim1_hierarchy 的行业值分离到 dim1_industry

背景：旧版 spec_prefix 的 detect_hierarchy 把「层级」与「行业」混在一个字段
（GB→国家标准、JGJ→建筑工程、JTG→公路工程 并存于 dim1_hierarchy）。
新语义：
  - dim1_hierarchy：纯层级（国家标准 / 行业标准 / 地方标准 / 团体标准 / 企业标准）
  - dim1_industry：行业归属（JGJ→建筑工程、JTG→公路工程，仅行业标准类有值）

本脚本按规范 code 用 detect_hierarchy / detect_industry 重算两字段。
对无法识别前缀的 code 跳过（保持原值），改动前建议先备份 SQLite。

运行：D:/Python/python.exe scripts/migrate_dim1_hierarchy.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_connection
from app.parser.spec_prefix import detect_hierarchy, detect_industry


def main():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, code, dim1_hierarchy, dim1_industry FROM specifications"
        ).fetchall()
        updated = skipped = 0
        for r in rows:
            h = detect_hierarchy(r["code"])
            ind = detect_industry(r["code"])
            if not h:
                skipped += 1
                print(f"  [跳过] code 无法识别前缀: {r['code']}")
                continue
            old = f"{r['dim1_hierarchy'] or ''}/{r['dim1_industry'] or ''}"
            new = f"{h}/{ind}"
            if old != new:
                conn.execute(
                    "UPDATE specifications SET dim1_hierarchy=?, dim1_industry=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (h, ind, r["id"]),
                )
                updated += 1
                print(f"  {r['code']}: {old or '(空)'} → {new}")
        conn.commit()
        print(f"\n共更新 {updated} 本规范，跳过 {skipped} 本。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
