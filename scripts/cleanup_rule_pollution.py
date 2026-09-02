"""规则污染诊断与保守清理工具

背景：历史 auto_adopted / feedback 用正则切词沉淀了「碎片规则」（无 label、匹配词被当标签），
导致条文被打上无意义标签（强行打标）。label 方案（2026-09）上线后新沉淀已带正确标签，
但历史污染仍存：
  - label 为 NULL 的规则（旧语义：pattern 当标签——可能是碎片，也可能是人工有意建的词，
    脚本无法自动区分，**删除与否需人工在规则页/此处清单判断**）
  - 被垃圾标签打标的条文：dim4/5/6 值无任何规则支撑 = 孤儿标签

用法（对当前库只读诊断，改库操作均需显式传参；改动前建议先备份 SQLite）：
  D:/Python/python.exe scripts/cleanup_rule_pollution.py                 # 诊断报告
  D:/Python/python.exe scripts/cleanup_rule_pollution.py --disable-unconfirmed
      # 停用 confirmed=0 且未锁定的规则（历史 auto_adopted 沉淀，多为碎片且无人工确认；
      # 禁用比删除安全可恢复；锁定 locked=1 的规则豁免，不在此列）
  D:/Python/python.exe scripts/cleanup_rule_pollution.py --reset-orphan-labels
      # 把 dim4/5/6 无启用规则支撑的标签条文重置为待复核（清标签、needs_review=1；
      # 仅动 clauses 条文，不触碰任何规则）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DIMS = [("dim4", "dim4_specialty", "所属专业"), ("dim5", "dim5_location", "工程部位"),
        ("dim6", "dim6_material", "材料/工艺")]


def _report(conn):
    print("═══ 诊断报告 ═══\n")

    print("【一】label 为 NULL 的规则（旧语义，pattern 当标签——可能碎片，人工判断是否删除）")
    for dim, _, label_cn in DIMS:
        rows = conn.execute(
            "SELECT id, pattern, hit_count, confirmed, is_active FROM classification_rules "
            "WHERE dimension=? AND label IS NULL ORDER BY hit_count DESC LIMIT 15",
            (dim,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM classification_rules WHERE dimension=? AND label IS NULL",
            (dim,),
        ).fetchone()[0]
        print(f"\n{label_cn}（共 {total} 条 label=NULL，显示命中最高 15 条）:")
        for r in rows:
            flag = "" if r["is_active"] else " [已停用]"
            print(f"  id={r['id']} pattern='{r['pattern']}' hit={r['hit_count']}/conf={r['confirmed']}{flag}")
        if not rows:
            print("  （无）")

    print("\n【二】孤儿标签条文（dim 值无任何规则 label/pattern 支撑——垃圾标签或 AI 直接打标）")
    for dim, col, label_cn in DIMS:
        rows = conn.execute(
            f"""SELECT c.id, c.clause_no, c.{col} AS v, s.code AS spec
                FROM clauses c JOIN specifications s ON c.spec_id = s.id
                WHERE c.{col} IS NOT NULL AND c.{col} != ''
                  AND NOT EXISTS (
                    SELECT 1 FROM classification_rules r
                    WHERE r.dimension = ? AND r.is_active = 1 AND (r.label = c.{col} OR (r.label IS NULL AND r.pattern = c.{col})))
                ORDER BY c.{col} LIMIT 20""",
            (dim,),
        ).fetchall()
        cnt = conn.execute(
            f"""SELECT COUNT(*) FROM clauses c
                WHERE c.{col} IS NOT NULL AND c.{col} != ''
                  AND NOT EXISTS (
                    SELECT 1 FROM classification_rules r
                    WHERE r.dimension = ? AND r.is_active = 1 AND (r.label = c.{col} OR (r.label IS NULL AND r.pattern = c.{col})))""",
            (dim,),
        ).fetchone()[0]
        print(f"\n{label_cn}（共 {cnt} 条孤儿，显示前 20）:")
        for r in rows:
            print(f"  id={r['id']} [{r['spec']} {r['clause_no']}] 值='{r['v']}'")
        if not rows:
            print("  （无）")


def _disable_unconfirmed(conn):
    """停用 confirmed=0 的规则（历史 auto_adopted 沉淀，无人工确认，多为碎片）

    合理规则通常有人工确认（confirmed>0，如 '结构' conf=14）；auto_adopted 只 hit++ 不
    confirmed，故 confirmed=0 的规则全是 AI 自动采纳沉淀——碎片为主。禁用止损（比删除安全）。
    """
    rows = conn.execute(
        "SELECT dimension, COUNT(*) n FROM classification_rules "
        "WHERE confirmed = 0 AND is_active = 1 AND (locked IS NULL OR locked = 0) "
        "GROUP BY dimension"
    ).fetchall()
    for r in rows:
        dim_label = {"dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"}.get(r["dimension"], r["dimension"])
        print(f"{dim_label}: 将停用 {r['n']} 条 confirmed=0 规则")
    conn.execute(
        "UPDATE classification_rules SET is_active = 0 "
        "WHERE confirmed = 0 AND is_active = 1 AND (locked IS NULL OR locked = 0)"
    )
    print("\n已停用全部 confirmed=0 规则（锁定规则豁免，碎片止损）。")


def _reset_orphan_labels(conn):
    total = 0
    for dim, col, label_cn in DIMS:
        n = conn.execute(
            f"""UPDATE clauses SET {col} = '', ai_classified = 0, needs_review = 1
                WHERE id IN (
                  SELECT c.id FROM clauses c
                  WHERE c.{col} IS NOT NULL AND c.{col} != ''
                    AND NOT EXISTS (
                      SELECT 1 FROM classification_rules r
                      WHERE r.dimension = ? AND r.is_active = 1 AND (r.label = c.{col} OR (r.label IS NULL AND r.pattern = c.{col}))))""",
            (dim,),
        ).rowcount
        print(f"{label_cn}: 已重置 {n} 条孤儿标签条文为待复核")
        total += n
    print(f"\n共重置 {total} 条。可运行规则页「运行 AI 分类」重新分类。")


def main():
    ap = argparse.ArgumentParser(description="规则污染诊断与保守清理")
    ap.add_argument("--disable-unconfirmed", action="store_true",
                    help="停用 confirmed=0 的规则（历史 auto_adopted 沉淀碎片止损）")
    ap.add_argument("--reset-orphan-labels", action="store_true",
                    help="重置 dim4/5/6 无规则支撑标签的条文为待复核（清标签）")
    args = ap.parse_args()

    from app.database import get_connection
    conn = get_connection()
    try:
        if args.disable_unconfirmed:
            _disable_unconfirmed(conn)
            conn.commit()
        elif args.reset_orphan_labels:
            _reset_orphan_labels(conn)
            conn.commit()
        else:
            _report(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
