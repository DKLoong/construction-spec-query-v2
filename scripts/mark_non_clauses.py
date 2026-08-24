"""为存量数据库中的非条文块打标 clause_is_non=1（幂等，可重跑）

背景：规范里的「前言」「目次」「条文说明」「本规程用词说明」等整块非条文
曾被当条文导入，污染检索排序、向量索引与精排。本脚本对存量库执行一次性迁移，
把 title（或 clause_no）命中黑名单规则的条文打上 clause_is_non=1 标记，
检索层将默认隐藏这些打标项。

注意：
- 判定规则与 app/parser/md_parser.is_non_clause_title 完全一致（共用同一函数）
- 只打标、不删除「目次/Contents」——删除动作需由人工审查后另行执行
- 幂等设计：重复执行不产生额外副作用
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db, init_db
from app.parser.md_parser import is_non_clause_title


def mark_non_clauses(conn):
    """扫描 clauses，对 title/clause_no 命中黑名单规则的条文打标 clause_is_non=1

    返回 (更新条数, 命中条文 id 列表)
    """
    rows = conn.execute(
        "SELECT id, title, clause_no FROM clauses"
    ).fetchall()
    updated_ids = []
    for r in rows:
        # title 与 clause_no 都按同一黑名单判定（兼容旧导入 clause_no 即为标题的情形）
        if is_non_clause_title(r["title"]) or is_non_clause_title(r["clause_no"]):
            conn.execute(
                "UPDATE clauses SET clause_is_non = 1 WHERE id = ?",
                (r["id"],),
            )
            updated_ids.append(r["id"])
    return len(updated_ids), updated_ids


def main():
    init_db()  # 幂等迁移：确保 clause_is_non 列存在（旧库需此步骤）
    with get_db() as conn:
        n, ids = mark_non_clauses(conn)
    print(f"已打标 {n} 条非条文")
    for cid in ids:
        print(f"  - clauses.id={cid}")
    print("完成。检索层默认隐藏这些打标项（include_non_clause=1 可放行）。")


if __name__ == "__main__":
    main()
