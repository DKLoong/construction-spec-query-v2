"""LanceDB 子块列（chunk_index）与重建探测。

超长条文要按 512 token 切子块写多行，故向量表新增 `chunk_index` 列；
现存表没有该列，`needs_rebuild()` 供维护页提前提示重建，
而不是等写入时才发现列不存在（列缺失的写入会静默落 NULL，见 C-8 说明）。
"""


def test_schema_includes_chunk_index():
    from app.search.vector_search import embedding_schema
    names = [f.name for f in embedding_schema(8)]
    assert names == ["clause_id", "spec_id", "text", "embedding", "dim_scores", "chunk_index"]


def test_old_table_without_chunk_index_is_rebuilt(tmp_path, monkeypatch):
    """现存表无 chunk_index → 必须重建（本批因内容变更重建，非「不支持 ALTER」，见 C-8）。"""
    import lancedb
    import pyarrow as pa
    # C-4：patch 的必须是消费方模块的名字（`vector_search.py:4` 是 from ... import）
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    db = lancedb.connect(str(tmp_path / "lance"))
    db.create_table("clause_embeddings", schema=pa.schema([
        pa.field("clause_id", pa.int64()), pa.field("spec_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), 4)),
        pa.field("dim_scores", pa.string()),
    ]))
    from app.search.vector_search import VectorStore
    vs = VectorStore()
    assert vs.needs_rebuild() is True


def test_up_to_date_table_needs_no_rebuild(tmp_path, monkeypatch):
    """正常：表由当前工厂建出（含 chunk_index）→ 不需重建（不得常态化误报）"""
    import lancedb
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    from app.search.vector_search import VectorStore, embedding_schema
    db = lancedb.connect(str(tmp_path / "lance"))
    db.create_table("clause_embeddings", schema=embedding_schema(4))
    assert VectorStore().needs_rebuild() is False


def test_missing_table_needs_no_rebuild(tmp_path, monkeypatch):
    """边界：表不存在 → False（「无表」由 index_missing 的 -1 表达，不属「缺列」）"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    from app.search.vector_search import VectorStore
    assert VectorStore().needs_rebuild() is False


def test_schema_read_failure_requests_rebuild(tmp_path, monkeypatch):
    """异常：schema 读不出来 → 保守返回 True 并留日志（宁可提示重建，不可静默漏列）"""
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    from app.search.vector_search import VectorStore

    class _BrokenTable:
        @property
        def schema(self):
            raise RuntimeError("表损坏")

    monkeypatch.setattr(VectorStore, "_table_exists", lambda self: True)
    monkeypatch.setattr(VectorStore, "_get_table", lambda self: _BrokenTable())
    assert VectorStore().needs_rebuild() is True
