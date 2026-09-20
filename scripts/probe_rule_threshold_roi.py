"""离线探针：规则阈值 / 计分公式对「补关键词」收益的影响（T7、T8 的实测依据）

回答的问题：人工大批量补 dim2~dim4 标签+关键词，对后续导入是否值得？
做法：拿库内全部激活规则，对全部条文实跑 classify_clause，统计每维得分分布；
      再用「1 次命中即 0.6」的反事实公式重算，对比达标条数。

要点（结论见 TODOS T7/T8）：
- 规则自身 threshold（0.5/0.6/0.7）决定「能否写入分类列」；
  ADAPTIVE_THRESHOLDS（0.5/0.6/0.7）决定「能否免掉 AI 队列」。两者常被混谈。
- keyword 得分 = min(1.0, 0.3 + 词频×0.15) × (1 + 0.1×priority)，同维取最佳单条不累加
  → 词出现 1 次得分 ≤0.36，达不到 0.5 阈值 = 既不写列也不免 AI（补词收益为 0）。

只读脚本，不改库。用法：
  D:/Python/python.exe scripts/probe_rule_threshold_roi.py
  # 在 git worktree 中运行时 data/ 为空，用环境变量指向主库：
  DATABASE_PATH=<主仓>/data/spec_query.db D:/Python/python.exe scripts/probe_rule_threshold_roi.py
"""
import collections
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier.rule_engine import classify_clause  # noqa: E402
from app.config import DATABASE_PATH  # noqa: E402
from app.params.registry import get_adaptive_thresholds  # noqa: E402

DIMS = ["dim2", "dim3", "dim4", "dim5", "dim6"]


def _flat_score(text: str, rule: dict) -> float:
    """反事实计分：1 次命中即 0.6（消除频次惩罚），用于对比公式改动收益"""
    count = text.count(rule["pattern"])
    if count == 0:
        return 0.0
    return min(1.0, 0.6 + 0.15 * (count - 1)) * (1 + 0.1 * rule.get("priority", 1))


def main() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rules = [dict(r) for r in conn.execute(
        "SELECT * FROM classification_rules WHERE is_active = 1")]
    clauses = list(conn.execute(
        "SELECT id, content, dim4_specialty FROM clauses ORDER BY id"))
    thresholds = get_adaptive_thresholds()

    print(f"库: {DATABASE_PATH}")
    print(f"激活规则 {len(rules)} 条 / 条文 {len(clauses)} 条")
    print(f"自适应阈值 {thresholds}")
    print(f"规则自身阈值分布 "
          f"{dict(collections.Counter(r['threshold'] for r in rules))}\n")

    stat = {d: collections.Counter() for d in DIMS}
    dim4_detail = []
    for c in clauses:
        scores, _labels, rule_ids = classify_clause(c["content"], [], rules)
        for d in DIMS:
            score = scores[d]
            if score == 0:
                stat[d]["无命中"] += 1
            elif score < thresholds[d]:
                stat[d]["命中但未达标(仍需AI)"] += 1
            else:
                stat[d]["达标(免AI)"] += 1
        if scores["dim4"] > 0:
            rule = next(r for r in rules if r["id"] == rule_ids["dim4"])
            dim4_detail.append((
                c["id"], rule["pattern"], c["content"].count(rule["pattern"]),
                round(scores["dim4"], 3), rule["label"], c["dim4_specialty"],
            ))

    print("=== 每维得分分布 ===")
    for d in DIMS:
        print(f"  {d}: " + " | ".join(f"{k}={v}" for k, v in stat[d].items()))

    print(f"\n=== dim4 命中明细（{len(dim4_detail)}/{len(clauses)}）===")
    print("  clause_id | 命中词 | 出现次数 | 得分 | 规则label | 列现值")
    for row in dim4_detail:
        print("  ", row)

    print("\n=== 反事实：1 次命中即 0.6 后各维达标条数 ===")
    for d in DIMS:
        passed = 0
        for c in clauses:
            best = 0.0
            for r in rules:
                if r["dimension"] != d or not r.get("is_active", 1):
                    continue
                score = _flat_score(c["content"], r)
                if score >= r.get("threshold", 0.6) and score > best:
                    best = score
            if best >= thresholds[d]:
                passed += 1
        now = stat[d]["达标(免AI)"]
        print(f"  {d}: 现在 {now} → 改后 {passed}")

    conn.close()


if __name__ == "__main__":
    main()
