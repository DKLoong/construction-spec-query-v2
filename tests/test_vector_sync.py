"""向量索引自愈（sync_with_db）测试"""
import logging
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
        for no, sp in (("1", "1 总则"), ("2", "2 术语和符号")):
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, content, section_path)"
                " VALUES (?,?,?,?)",
                (spec_id, no, "x", sp),
            )
    calls = []
    monkeypatch.setattr(
        VectorStore, "index_clause_chunks",
        lambda self, clause_id, spec_id, texts, dim_scores="": calls.append((clause_id, texts)),
    )
    added = VectorStore().index_missing()
    assert added == 1  # 仅 clause_id=2 缺失
    assert [c[0] for c in calls] == [2]
    # 补齐路径的向量文本**必须**含面包屑：漏传面包屑是静默的（build_embed_chunks
    # 的关键字参数有默认值 ""），而补齐是全量重建之外的唯一自动修补通道，
    # 漏了会让这些条文永远缺位置信号。
    assert calls[0][1] == ["GB T t [2] 2 术语和符号 x"]


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


# ---------- clause_id 只读该列（R1：lance 数据集列投影） ----------

def _setup_lance(monkeypatch, tmp_path) -> str:
    """patch 消费方模块名 + 建库，返回 lance 目录"""
    db_path = tmp_path / "test.db"
    lance_path = tmp_path / "lance"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(lance_path))
    init_db()
    return str(lance_path)


def test_iter_clause_ids_matches_table_column(monkeypatch, tmp_path):
    """行为等价：返回集合与直接读该列一致，且同 clause_id 多块（子块）天然去重"""
    lance_path = _setup_lance(monkeypatch, tmp_path)
    # (1,1) 出现两次 = 一条超长条文切出的两个子块
    tbl = _make_table(lance_path, [(1, 1, "a"), (1, 1, "a-块2"), (2, 1, "b"), (5, 1, "c")])
    expected = {int(v) for v in tbl.to_arrow().column("clause_id").to_pylist()}
    assert expected == {1, 2, 5}, "夹具本身应有重复行"

    assert VectorStore().iter_clause_ids() == expected


def test_iter_clause_ids_projects_only_clause_id_column(monkeypatch, tmp_path):
    """R1：必须走 lance 数据集的**列投影**，只请求 clause_id 一列。

    `Table.to_arrow()` 在本机 lancedb 0.17.0 无 columns 参数，而
    `to_arrow().select([...])` 只是 pyarrow 视图（数据已全量物化）——
    实测真实库 971 行：全量 2.55 MB vs 仅 clause_id 0.0078 MB（约 330×）。
    故用探针直接钉住「真正向 lance 请求的列」，而非只看源码里有没有某个字符串。
    """
    import lance
    lance_path = _setup_lance(monkeypatch, tmp_path)
    _make_table(lance_path, [(1, 1, "a"), (2, 1, "b"), (5, 1, "c")])

    requested: list = []
    real_dataset = lance.dataset

    def spy_dataset(path, *a, **kw):
        ds = real_dataset(path, *a, **kw)
        real_to_table = ds.to_table

        class _Wrapped:
            def to_table(self, *ta, **tkw):
                requested.append(tkw.get("columns"))
                return real_to_table(*ta, **tkw)

        return _Wrapped()

    monkeypatch.setattr(lance, "dataset", spy_dataset)

    assert VectorStore().iter_clause_ids() == {1, 2, 5}
    assert requested == [["clause_id"]], f"必须按列投影只读 clause_id，实际={requested}"


def test_iter_clause_ids_missing_table_returns_empty(monkeypatch, tmp_path):
    """表不存在 → 空集（不抛异常）"""
    _setup_lance(monkeypatch, tmp_path)
    assert VectorStore().iter_clause_ids() == set()


def test_read_clause_ids_returns_none_on_read_failure(monkeypatch, tmp_path, caplog):
    """「空表」与「读失败」必须可区分：前者是正常态（新库无条文），
    后者若被当成空集，`index_missing()` 会误判「全部条文都缺向量」而重嵌整个语料、
    健康检查会把「读失败」误报成「正常」。

    ⇒ `read_clause_ids()` 返回 None 表读失败（并记日志），`iter_clause_ids()` 在其上
    降级为空集。
    """
    _setup_lance(monkeypatch, tmp_path)
    vs = VectorStore()
    # 表「存在」（绕过 lancedb 探测）但 lance 数据集打不开 —— 读失败
    monkeypatch.setattr(vs, "_table_exists", lambda: True)

    with caplog.at_level(logging.WARNING, logger="app.search.vector_search"):
        assert vs.read_clause_ids() is None
        assert vs.iter_clause_ids() == set()

    assert any("clause_id" in r.getMessage() for r in caplog.records), \
        "读失败必须留下日志（禁止无日志的静默降级）"


def test_index_missing_read_failure_returns_minus_one(monkeypatch, tmp_path):
    """既有契约不退化：读失败时 index_missing 仍返回 -1（「需全量重建」）。

    若把读失败降级成空集，这里会把**全部**条文当成缺失，从维护页点一次
    「修复」就会白跑一遍全语料嵌入（而且写完仍是坏的）。
    """
    _setup_lance(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB T', 't')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, content) VALUES (?,?,?)",
            (spec_id, "1", "x"),
        )
    vs = VectorStore()
    monkeypatch.setattr(vs, "_table_exists", lambda: True)
    assert vs.index_missing() == -1
