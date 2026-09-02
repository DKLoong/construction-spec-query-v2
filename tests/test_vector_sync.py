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


def test_index_missing_no_table(monkeypatch, tmp_path):
    """向量表不存在时 index_missing 返回 -1（供 fix_issue 识别「需重建」）"""
    db_path = tmp_path / "test.db"
    lance_path = tmp_path / "lance"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(lance_path))
    init_db()
    assert VectorStore().index_missing() == -1


def test_index_missing_diffset_no_placeholders(monkeypatch, tmp_path):
    """Python 侧差集补齐缺失向量（不依赖 NOT IN 动态占位符）"""
    db_path = tmp_path / "test.db"
    lance_path = tmp_path / "lance"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(lance_path))
    init_db()
    _make_table(lance_path, [(1, 1, "a")])  # 向量表仅含 clause_id=1
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB T', 't')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for no in ("1", "2"):
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?,?,?)",
                (spec_id, no, "x"),
            )
    calls = []
    monkeypatch.setattr(
        VectorStore, "index_clause",
        lambda self, clause_id, spec_id, text, dim_scores="": calls.append((clause_id, text)),
    )
    added = VectorStore().index_missing()
    assert added == 1  # 仅 clause_id=2 缺失
    assert [c[0] for c in calls] == [2]
    from app.search.embed_text import build_embed_text
    assert calls[0][1] == build_embed_text("GB T", "t", "2", "", "x")


def test_batch_index_progress_cb(monkeypatch, tmp_path):
    """batch_index 每批回调 progress_cb(done, total)，末批 done=total"""
    lance_path = tmp_path / "lance-bi"
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(lance_path))

    import numpy as np
    def fake_embed_texts(texts):
        return [np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32) for _ in texts]
    monkeypatch.setattr("app.search.vector_search.embed_texts", fake_embed_texts)

    clauses = [
        {"clause_id": i, "spec_id": 1, "text": f"文本{i}", "dim_scores": ""}
        for i in range(1, 71)
    ]
    calls: list[tuple[int, int]] = []
    VectorStore().batch_index(clauses, batch_size=32,
                              progress_cb=lambda d, t: calls.append((d, t)))
    # first 32 → (32,70)；循环 (64,70)、(70,70)
    assert len(calls) == 3
    assert calls[0] == (32, 70)
    assert calls[-1] == (70, 70)
    assert VectorStore()._table_exists()
    assert VectorStore()._get_table().to_arrow().num_rows == 70
