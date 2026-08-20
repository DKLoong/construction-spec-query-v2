"""向量索引自愈（sync_with_db）测试"""
import lancedb
import numpy as np
import pyarrow as pa

from app.database import init_db, get_db
from app.search.vector_search import VectorStore


def _make_table(lance_path, records):
    """用固定维度零向量直接建表，避免加载 BGE 模型"""
    schema = pa.schema([
        pa.field("clause_id", pa.int64()),
        pa.field("spec_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), 4)),
        pa.field("dim_scores", pa.string()),
    ])
    db = lancedb.connect(str(lance_path))
    tbl = db.create_table("clause_embeddings", schema=schema)
    tbl.add([
        {"clause_id": rid, "spec_id": sid, "text": txt,
         "embedding": np.zeros(4, dtype=np.float32), "dim_scores": ""}
        for rid, sid, txt in records
    ])
    return tbl


def test_sync_with_db_removes_orphans(monkeypatch, tmp_path):
    """向量表中 DB 不存在的 clause_id 应被清理"""
    db_path = tmp_path / "test.db"
    lance_path = tmp_path / "lance"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(lance_path))
    init_db()
    _make_table(lance_path, [(1, 1, "a"), (2, 1, "b"), (3, 1, "c"), (4, 1, "d")])
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB T', 't')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for no in ("1", "2"):
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?,?,?)",
                (spec_id, no, "x"),
            )

    removed = VectorStore().sync_with_db()
    assert removed == 2  # clause_id 3、4 为孤儿
    remaining = {
        int(v)
        for v in VectorStore()._get_table().to_arrow().column("clause_id").to_pylist()
    }
    assert remaining == {1, 2}


def test_sync_with_db_no_orphans(monkeypatch, tmp_path):
    """向量表与 DB 完全一致时返回 0"""
    db_path = tmp_path / "test.db"
    lance_path = tmp_path / "lance"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(lance_path))
    init_db()
    _make_table(lance_path, [(1, 1, "a")])
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB T', 't')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?,?,?)",
            (spec_id, "1", "x"),
        )
    assert VectorStore().sync_with_db() == 0


def test_sync_with_db_no_table(monkeypatch, tmp_path):
    """向量表不存在时返回 0"""
    db_path = tmp_path / "test.db"
    lance_path = tmp_path / "lance"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(lance_path))
    init_db()
    assert VectorStore().sync_with_db() == 0
