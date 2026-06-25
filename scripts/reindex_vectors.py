"""为 SQLite 中已有条文重建 LanceDB 向量索引"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db
from app.search.vector_search import VectorStore
from app.ai.embedding import get_model


def main():
    # 检查模型是否可用
    model = get_model()
    if model is None:
        print("❌ Embedding 模型不可用，请先下载 BGE-small-zh-v1.5")
        sys.exit(1)

    # 获取所有条文
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id as clause_id, c.spec_id, c.clause_no, c.title, c.content,
                      c.dim4_specialty, c.dim5_location, c.dim6_material
               FROM clauses c
               ORDER BY c.id"""
        ).fetchall()

    if not rows:
        print("⚠️ 数据库中没有条文，请先导入规范")
        return

    print(f"共有 {len(rows)} 条条文，开始重建向量索引...")

    vs = VectorStore()
    indexed = 0
    for r in rows:
        text = f"[{r['clause_no']}] {r['title'] or ''} {r['content']}"
        # 构建维度分数字符串
        dim_parts = []
        if r["dim4_specialty"]:
            dim_parts.append(f"dim4={r['dim4_specialty']}")
        if r["dim5_location"]:
            dim_parts.append(f"dim5={r['dim5_location']}")
        if r["dim6_material"]:
            dim_parts.append(f"dim6={r['dim6_material']}")
        dim_scores = ",".join(dim_parts)

        try:
            # 先删旧索引（如果存在）
            vs.delete_clause(r["clause_id"])
            # 重新索引
            vs.index_clause(r["clause_id"], r["spec_id"], text, dim_scores)
            indexed += 1
            print(f"  [{indexed}/{len(rows)}] {r['clause_no']} — {r['content'][:40]}...")
        except Exception as e:
            print(f"  ⚠️ [{r['clause_no']}] 索引失败: {e}")

    print(f"\n✅ 完成：{indexed}/{len(rows)} 条已索引")


if __name__ == "__main__":
    main()
