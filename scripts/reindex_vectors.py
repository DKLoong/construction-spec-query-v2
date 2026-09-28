"""为 SQLite 中已有条文重建 LanceDB 向量索引 — 完全清空旧表后重建"""
import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db
from app.search.vector_search import VectorStore
from app.search.chunking import build_embed_chunks
from app.ai.embedding import get_model


def main():
    model = get_model()
    if model is None:
        print("ERROR: Embedding model not available. Please download BGE-small-zh-v1.5 first.")
        sys.exit(1)

    # 获取所有条文（含规范名称）
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id as clause_id, c.spec_id, c.clause_no, c.title, c.content,
                      c.section_path,
                      c.dim4_specialty, c.dim5_location, c.dim6_material,
                      s.code as spec_code, s.title as spec_title
               FROM clauses c
               JOIN specifications s ON c.spec_id = s.id
               ORDER BY c.id"""
        ).fetchall()

    if not rows:
        print("WARNING: No clauses in database. Please import specifications first.")
        return

    print(f"Total clauses: {len(rows)}")
    print("Rebuilding vector index from scratch...")
    print()

    # 构建嵌入文本：走共享的 build_embed_chunks（含 plain_text 去标记、与导入路径同格式，
    # 且**承担前缀预算预留**）。此处曾内联重复拼接，导致与导入路径漂移、且会绕过标记清洗；
    # 重建若只写单行，超长条文的尾部还会永久不可召回，切勿再内联。
    clauses = []
    for r in rows:
        # 构建维度分数字符串
        dim_parts = []
        if r["dim4_specialty"]:
            dim_parts.append(f"dim4={r['dim4_specialty']}")
        if r["dim5_location"]:
            dim_parts.append(f"dim5={r['dim5_location']}")
        if r["dim6_material"]:
            dim_parts.append(f"dim6={r['dim6_material']}")
        dim_scores = ",".join(dim_parts)

        # 超长条文切块 → 每块一条记录（chunk_index 递增）
        for ci, text in enumerate(build_embed_chunks(
                r["content"] or "", code=r["spec_code"] or "", spec_title=r["spec_title"] or "",
                clause_no=r["clause_no"] or "", clause_title=r["title"] or "",
                section_path=r["section_path"] or "")):
            clauses.append({
                "clause_id": r["clause_id"],
                "spec_id": r["spec_id"],
                "text": text,
                "dim_scores": dim_scores,
                "chunk_index": ci,
            })

    # 批量索引（内部会先 clear_all 再建表）
    vs = VectorStore()
    vs.batch_index(clauses)

    print()
    print(f"Done: {len(clauses)} clauses reindexed successfully.")
    print()

    # 验证
    tbl = vs._get_table()
    print(f"LanceDB table rows: {tbl.count_rows()}")
    print(f"Verification OK: {tbl.count_rows() == len(clauses)}")


if __name__ == "__main__":
    # Force UTF-8 output to avoid GBK encoding errors on Windows。
    # 只在 CLI 直跑时包裹：测试会 import 本模块调用 main()，模块级包裹会破坏 pytest 的
    # stdout 捕获（把捕获对象再包一层 TextIOWrapper）。
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
