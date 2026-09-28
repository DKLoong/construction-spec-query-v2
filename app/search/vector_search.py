import logging
import lancedb
import pyarrow as pa
from app.config import LANCE_DB_PATH
from app.database import get_db
from app.ai.embedding import embed_texts
from app.search.embed_text import build_embed_text

logger = logging.getLogger(__name__)


def embedding_schema(dim: int) -> pa.Schema:
    """`clause_embeddings` 的**唯一** schema 定义处。

    半精度向量列必须显式声明固定长度（`pa.list_(pa.float32(), dim)`），
    否则 LanceDB 推断出的列类型无法做向量检索。三处建表路径
    （index_clause / batch_index / 导入首建）全部走本函数。
    """
    return pa.schema([
        pa.field("clause_id", pa.int64()),
        pa.field("spec_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), dim)),
        pa.field("dim_scores", pa.string()),
    ])


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
            schema = embedding_schema(len(emb))
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

    def get_orphans(self) -> list[int]:
        """返回向量表有而 SQLite 无的孤儿 clause_id（只读，不删除）"""
        if not self._table_exists():
            return []
        try:
            rows = self._get_table().to_arrow()
        except Exception as e:
            logger.warning("读取向量表 clause_id 失败: %s", e)
            return []
        if rows.num_rows == 0:
            return []
        vector_ids = {int(v) for v in rows.column("clause_id").to_pylist()}
        with get_db() as conn:
            db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}
        return sorted(vector_ids - db_ids)

    def sync_with_db(self) -> int:
        """对比向量表与数据库现存条文，删除孤儿向量（返回清理条数）

        用于自愈历史遗留或删除未同步的向量记录，避免旧数据污染语义检索候选池。
        仅比对 clause_id，不涉及 embedding，开销轻量，可在启动时/删除后调用。
        """
        orphans = self.get_orphans()
        if not orphans:
            return 0
        tbl = self._get_table()
        removed = 0
        for cid in orphans:
            try:
                tbl.delete(f"clause_id = {cid}")
                removed += 1
            except Exception as e:
                logger.warning("清理孤儿向量 clause_id=%s 失败: %s", cid, e)
        return removed

    def index_missing(self) -> int:
        """SQLite 有而向量表无的条文补索引（返回补齐数）。

        向量表不存在/读取失败返回 -1（区别于「无需补齐」的 0），供调用方
        识别「需全量重建」而非「单项修复」。

        缺失集在 Python 侧做差集：先全量查 SQLite 条文，再过滤不在 vector_ids
        中的记录，避免 NOT IN 动态占位符数量超过 SQLite 变量上限（32766）。
        """
        if not self._table_exists():
            return -1
        try:
            rows = self._get_table().to_arrow()
            vector_ids = {int(v) for v in rows.column("clause_id").to_pylist()} if rows.num_rows else set()
        except Exception as e:
            logger.warning("读取向量表失败: %s", e)
            return -1
        with get_db() as conn:
            all_rows = conn.execute(
                """SELECT c.id, c.spec_id, c.clause_no, c.title, c.content,
                          s.code, s.title as spec_title
                   FROM clauses c JOIN specifications s ON c.spec_id = s.id
                   ORDER BY c.id"""
            ).fetchall()
        missing = [r for r in all_rows if r["id"] not in vector_ids]
        added = 0
        for r in missing:
            embed_text = build_embed_text(
                r["code"], r["spec_title"], r["clause_no"], r["title"], r["content"])
            try:
                self.index_clause(r["id"], r["spec_id"], embed_text)
                added += 1
            except Exception as e:
                logger.warning("补齐向量 clause_id=%s 失败: %s", r["id"], e)
        return added

    def delete_clause(self, clause_id: int):
        if self._table_exists():
            self._get_table().delete(f"clause_id = {clause_id}")

    def clear_all(self):
        """删除整个向量表及磁盘文件，用于完全重建索引

        磁盘路径的**唯一来源是本实例实际连接的库**（self.db.uri），
        绝不重新读 app.config.LANCE_DB_PATH——历史实现那样写会与 self.db
        的来源脱钩（测试只 patch 模块级常量，self.db 连临时目录而 rmtree
        落到真实库），导致跑测试静默删掉开发机真实向量表。
        """
        import shutil
        from pathlib import Path

        # 先通过 LanceDB API 删表
        if self._table_exists():
            self.db.drop_table("clause_embeddings")

        # 清理可能残留的 WAL/日志文件，防止旧数据被重放到新表
        conn_dir = Path(self.db.uri).resolve()
        table_dir = conn_dir / "clause_embeddings.lance"
        # 守卫：解析后必须仍落在本实例库目录内，否则拒绝删除。
        # 当前拼接方式恒真，此判断防的是将来改成动态路径时静默越界删库。
        if not table_dir.is_relative_to(conn_dir):
            logger.warning("拒绝删除越界的向量表目录: %s（本实例库: %s）",
                           table_dir, conn_dir)
            return
        if table_dir.exists():
            logger.info("清理向量表目录: %s", table_dir)
            shutil.rmtree(table_dir, ignore_errors=True)

    def batch_index(self, clauses: list[dict], batch_size: int = 32,
                    progress_cb=None):
        """批量索引条文（先建表再逐批插入）

        clauses: [{"clause_id": int, "spec_id": int, "text": str, "dim_scores": str}, ...]
        progress_cb(done: int, total: int) — 每处理完一批回调一次（供后台重建显示进度）
        """
        import numpy as np

        if not clauses:
            return
        total = len(clauses)

        # 先清空旧表
        self.clear_all()

        # 计算第一批嵌入以确定向量维度
        first_batch = clauses[:batch_size]
        first_embs = embed_texts([c["text"] for c in first_batch])
        vec_dim = len(first_embs[0])

        # 建表
        schema = embedding_schema(vec_dim)
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
        if progress_cb:
            progress_cb(len(first_batch), total)

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
            if progress_cb:
                progress_cb(min(start + batch_size, total), total)
