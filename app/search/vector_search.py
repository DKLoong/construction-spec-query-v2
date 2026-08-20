import logging
import lancedb
import pyarrow as pa
from app.config import LANCE_DB_PATH
from app.database import get_db
from app.ai.embedding import embed_texts

logger = logging.getLogger(__name__)


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

        # 先删旧记录，防止同一 clause_id 重复出现
        if self._table_exists():
            try:
                self._get_table().delete(f"clause_id = {clause_id}")
            except Exception:
                pass

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

    def sync_with_db(self) -> int:
        """对比向量表与数据库现存条文，删除孤儿向量（返回清理条数）

        用于自愈历史遗留或删除未同步的向量记录，避免旧数据污染语义检索候选池。
        仅比对 clause_id，不涉及 embedding，开销轻量，可在启动时/删除后调用。
        """
        if not self._table_exists():
            return 0
        tbl = self._get_table()
        try:
            rows = tbl.to_arrow()
        except Exception as e:
            logger.warning("读取向量表 clause_id 失败: %s", e)
            return 0
        if rows.num_rows == 0:
            return 0

        vector_ids = {int(v) for v in rows.column("clause_id").to_pylist()}
        with get_db() as conn:
            db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}

        orphans = sorted(vector_ids - db_ids)
        removed = 0
        for cid in orphans:
            try:
                tbl.delete(f"clause_id = {cid}")
                removed += 1
            except Exception as e:
                logger.warning("清理孤儿向量 clause_id=%s 失败: %s", cid, e)
        return removed

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
