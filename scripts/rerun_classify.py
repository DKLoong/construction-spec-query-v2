"""对数据库中已有条文重新执行规则匹配，更新 hit_count/confirmed"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db
from app.classifier.rule_engine import classify_clause
from app.config import ADAPTIVE_THRESHOLDS


def rerun():
    with get_db() as conn:
        # 1. 加载所有启用的规则
        rules_rows = conn.execute(
            "SELECT * FROM classification_rules WHERE is_active = 1"
        ).fetchall()
        rules = [dict(r) for r in rules_rows]
        print(f"加载 {len(rules)} 条启用规则")

        # 2. 重置所有规则的 hit_count 和 confirmed
        conn.execute(
            "UPDATE classification_rules SET hit_count = 0, confirmed = 0"
        )
        print("已重置所有规则的命中统计")

        # 3. 遍历所有规范 → 规范级分类 (dim2/dim3)
        specs = conn.execute(
            "SELECT id, code, title FROM specifications ORDER BY id"
        ).fetchall()
        print(f"\n扫描 {len(specs)} 个规范…")

        spec_hits = {"dim2": 0, "dim3": 0}
        for spec in specs:
            spec_text = f"{spec['code'] or ''} {spec['title'] or ''}"
            _, best_labels, best_rule_ids = classify_clause(spec_text, [], rules)
            for dim in ("dim2", "dim3"):
                rule_id = best_rule_ids.get(dim)
                label = best_labels.get(dim, "")
                if rule_id:
                    conn.execute(
                        "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                        (rule_id,),
                    )
                    spec_hits[dim] += 1
                    # 回写规范表
                    col = {"dim2": "dim2_stage", "dim3": "dim3_usage"}[dim]
                    conn.execute(
                        f"UPDATE specifications SET {col} = ? WHERE id = ?",
                        (label, spec["id"]),
                    )

        print(f"  规范级分类: dim2 命中 {spec_hits['dim2']}, dim3 命中 {spec_hits['dim3']}")

        # 4. 遍历所有条文 → 条文级分类 (dim4/dim5/dim6)
        clauses = conn.execute(
            """SELECT c.id, c.content, c.dim4_specialty, c.dim5_location, c.dim6_material,
                      s.code as spec_code, s.title as spec_title
               FROM clauses c
               JOIN specifications s ON c.spec_id = s.id
               ORDER BY c.id"""
        ).fetchall()
        print(f"\n扫描 {len(clauses)} 条条文…")

        dim_hits = {"dim4": 0, "dim5": 0, "dim6": 0}
        dim_queued = {"dim4": 0, "dim5": 0, "dim6": 0}

        for clause in clauses:
            scores, best_labels, best_rule_ids = classify_clause(
                clause["content"], [], rules
            )

            for dim in ("dim4", "dim5", "dim6"):
                rule_id = best_rule_ids.get(dim)
                label = best_labels.get(dim, "")

                if not rule_id:
                    # 没有规则匹配到，但条文可能已有手工标注的值
                    continue

                threshold = ADAPTIVE_THRESHOLDS.get(dim, 0.6)
                if scores[dim] >= threshold:
                    # 达标 → 命中 + 确认，并回写条文
                    conn.execute(
                        "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                        (rule_id,),
                    )
                    col = {"dim4": "dim4_specialty", "dim5": "dim5_location", "dim6": "dim6_material"}[dim]
                    conn.execute(
                        f"UPDATE clauses SET {col} = ? WHERE id = ?",
                        (label, clause["id"]),
                    )
                    dim_hits[dim] += 1
                else:
                    # 未达标 → 仅记录命中
                    conn.execute(
                        "UPDATE classification_rules SET hit_count = hit_count + 1 WHERE id = ?",
                        (rule_id,),
                    )
                    dim_queued[dim] += 1

        print(f"  条文级分类:")
        for dim in ("dim4", "dim5", "dim6"):
            total = dim_hits[dim] + dim_queued[dim]
            print(f"    {dim}: 自动采用 {dim_hits[dim]}, 进队列 {dim_queued[dim]}, 合计 {total}")

        # 5. 输出结果汇总
        print(f"\n{'='*50}")
        print("最终规则命中排行（top 20）：")
        rows = conn.execute(
            """SELECT dimension, pattern, hit_count, confirmed
               FROM classification_rules
               WHERE hit_count > 0
               ORDER BY hit_count DESC, dimension
               LIMIT 20"""
        ).fetchall()
        for r in rows:
            print(f"  {r['dimension']:6s} | hit={r['hit_count']:3d}  conf={r['confirmed']:3d}  | {r['pattern']}")

        zero = conn.execute(
            "SELECT COUNT(*) as c FROM classification_rules WHERE hit_count = 0"
        ).fetchone()
        if zero["c"]:
            print(f"\n  另有 {zero['c']} 条规则未命中任何条文")

        print("\n完成!")


if __name__ == "__main__":
    rerun()
