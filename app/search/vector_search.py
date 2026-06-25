import lancedb
import pyarrow as pa
from app.config import LANCE_DB_PATH
from app.ai.embedding import embed_texts


class VectorStore:
    def __init__(self):
        self.db = lancedb.connect(LANCE_DB_PATH)

    def _table_exists(self) -> bool:
        try:
            return "clause_embeddings" in self.db.table_names()
        except Exception:
            return False

    def _get_table(self):
        return self.db.open_table("clause_embeddings")

    def index_clause(self, clause_id: int, spec_id: int, text: str, dim_scores: str = ""):
        import numpy as np
        import pyarrow as pa
        vectors = embed_texts([text])
        emb = np.array(vectors[0], dtype=np.float32)

        if not self._table_exists():
            # 显式指定 schema，确保 embedding 列是固定大小向量类型
            schema = pa.schema([
                pa.field("clause_id", pa.int64()),
                pa.field("spec_id", pa.int64()),
                pa.field("text", pa.string()),
                pa.field("embedding", pa.list_(pa.float32(), len(emb))),
                pa.field("dim_scores", pa.string()),
            ])
            tbl = self.db.create_table("clause_embeddings", schema=schema)
            tbl.add([{
                "clause_id": clause_id,
                "spec_id": spec_id,
                "text": text,
                "embedding": emb,
                "dim_scores": dim_scores,
            }])
        else:
            self._get_table().add([{
                "clause_id": clause_id,
                "spec_id": spec_id,
                "text": text,
                "embedding": emb,
                "dim_scores": dim_scores,
            }])

    def search(self, query_text: str, top_k: int = 10,
               dim_filter: str | None = None) -> list[dict]:
        if not self._table_exists():
            return []
        import numpy as np
        q_vec = embed_texts([query_text])[0]
        q_vec = np.array(q_vec, dtype=np.float32)
        tbl = self._get_table()
        results = tbl.search(q_vec, vector_column_name="embedding").limit(top_k).to_list()
        return [
            {"clause_id": r["clause_id"], "spec_id": r["spec_id"],
             "text": r["text"], "_distance": r.get("_distance", 0)}
            for r in results
        ]

    def delete_clause(self, clause_id: int):
        if self._table_exists():
            self._get_table().delete(f"clause_id = {clause_id}")

    def clear_all(self):
        """删除整个向量表及磁盘文件，用于完全重建索引"""
        import shutil
        from pathlib import Path
        from app.config import LANCE_DB_PATH

        # 先通过 LanceDB API 删表
        if self._table_exists():
            self.db.drop_table("clause_embeddings")

        # 清理可能残留的 WAL/日志文件，防止旧数据被重放到新表
        table_dir = Path(LANCE_DB_PATH) / "clause_embeddings.lance"
        if table_dir.exists():
            shutil.rmtree(table_dir, ignore_errors=True)

    def batch_index(self, clauses: list[dict], batch_size: int = 32):
        """批量索引条文（先建表再逐批插入）

        clauses: [{"clause_id": int, "spec_id": int, "text": str, "dim_scores": str}, ...]
        """
        import numpy as np

        if not clauses:
            return

        # 先清空旧表
        self.clear_all()

        # 计算第一批嵌入以确定向量维度
        first_batch = clauses[:batch_size]
        first_embs = embed_texts([c["text"] for c in first_batch])
        vec_dim = len(first_embs[0])

        # 建表
        schema = pa.schema([
            pa.field("clause_id", pa.int64()),
            pa.field("spec_id", pa.int64()),
            pa.field("text", pa.string()),
            pa.field("embedding", pa.list_(pa.float32(), vec_dim)),
            pa.field("dim_scores", pa.string()),
        ])
        tbl = self.db.create_table("clause_embeddings", schema=schema)

        # 写入第一批
        records = []
        for i, c in enumerate(first_batch):
            records.append({
                "clause_id": c["clause_id"],
                "spec_id": c["spec_id"],
                "text": c["text"],
                "embedding": np.array(first_embs[i], dtype=np.float32),
                "dim_scores": c.get("dim_scores", ""),
            })
        tbl.add(records)

        # 逐批写入剩余
        for start in range(batch_size, len(clauses), batch_size):
            batch = clauses[start:start + batch_size]
            embs = embed_texts([c["text"] for c in batch])
            records = []
            for i, c in enumerate(batch):
                records.append({
                    "clause_id": c["clause_id"],
                    "spec_id": c["spec_id"],
                    "text": c["text"],
                    "embedding": np.array(embs[i], dtype=np.float32),
                    "dim_scores": c.get("dim_scores", ""),
                })
            tbl.add(records)
