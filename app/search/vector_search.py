import lancedb
import pyarrow as pa
from app.config import LANCE_DB_PATH
from app.ai.embedding import embed_texts


class VectorStore:
    def __init__(self):
        self.db = lancedb.connect(LANCE_DB_PATH)

    def _table_exists(self) -> bool:
        try:
            tables = self.db.list_tables()
            if hasattr(tables, "tables"):
                names = tables.tables
            else:
                names = list(tables)
            return "clause_embeddings" in names
        except Exception:
            return False

    def _get_table(self):
        return self.db.open_table("clause_embeddings")

    def index_clause(self, clause_id: int, spec_id: int, text: str, dim_scores: str = ""):
        vectors = embed_texts([text])
        data = [{
            "clause_id": clause_id,
            "spec_id": spec_id,
            "text": text,
            "embedding": vectors[0],
            "dim_scores": dim_scores,
        }]
        if self._table_exists():
            self._get_table().add(data)
        else:
            self.db.create_table("clause_embeddings", data)

    def search(self, query_text: str, top_k: int = 10,
               dim_filter: str | None = None) -> list[dict]:
        if not self._table_exists():
            return []
        q_vec = embed_texts([query_text])[0]
        tbl = self._get_table()
        results = tbl.search(q_vec).limit(top_k).to_list()
        return [
            {"clause_id": r["clause_id"], "spec_id": r["spec_id"],
             "text": r["text"], "_distance": r.get("_distance", 0)}
            for r in results
        ]

    def delete_clause(self, clause_id: int):
        if self._table_exists():
            self._get_table().delete(f"clause_id = {clause_id}")
