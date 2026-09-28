"""LanceDB 子块列（chunk_index）、切块实现与重建探测。

超长条文要按 512 **字符**切子块写多行（口径是字符，不是 token：实测算的是
`len()`；见 `app/search/chunking.py` 的说明），故向量表新增 `chunk_index` 列；
现存表没有该列，`needs_rebuild()` 是**为探测这种旧表**而写的原语（列缺失的写入会
静默落 NULL，见 C-8 说明）。

⚠ 定位要说准：`needs_rebuild()` **当前零消费方**（实测 `grep -rn needs_rebuild app/`
只有定义）——计划里「维护页据此提示重建」**未实现**，收口裁定见
`app/search/vector_search.py::needs_rebuild` 的 docstring（U7/R23/R26）。
本批真正的重建门禁是**全量重建 + 验收跑**，不是这个探测。
"""

from pathlib import Path

from app.config import CHUNK_CHAR_LIMIT


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


# ═══════════════════════════════════════════
# 超长条文切子块（Task 8：字符口径 + 句末对齐重叠 + 前缀预算预留）
# ═══════════════════════════════════════════

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _overlap_len(prev: str, cur: str) -> int:
    """相邻两块的**真实**重叠 = prev 的后缀与 cur 的前缀相同的最长长度。"""
    for k in range(min(len(prev), len(cur)), 0, -1):
        if prev[-k:] == cur[:k]:
            return k
    return 0


def test_chunk_short_text_returns_single_chunk():
    from app.search.chunking import chunk_text
    assert chunk_text("短正文。") == ["短正文。"]


def test_chunk_empty_returns_empty_list():
    from app.search.chunking import chunk_text
    assert chunk_text("") == []


def test_chunk_long_text_splits_on_sentence_boundary():
    """切点必须落在句子边界上，不得从词中间切断。

    ⚠ **R6：不变量已由「不丢不重（拼接等于原文）」改为「覆盖性 + 重叠量上限」**——
    加了重叠之后 `"".join(chunks) == text` 在数学上必然不成立
    （`["ABCD","CDEF"]` 拼回 `"ABCDCDEF"`）。两句分开断言，各自可证伪。
    """
    from app.search.chunking import chunk_text
    text = "。".join(f"第{i}句内容" for i in range(400)) + "。"
    chunks = chunk_text(text, limit=100)
    assert len(chunks) > 1
    assert all(c.endswith("。") for c in chunks), "块必须在句末标点处收尾"
    # ① 覆盖性：原文每个字符至少出现在一个块里（不丢）
    covered = "".join(chunks)
    assert all(ch in covered for ch in set(text)), "有字符凭空消失"
    assert chunks[0] == text[:len(chunks[0])], "首块必须从原文开头起"
    assert chunks[-1] == text[-len(chunks[-1]):], "末块必须到原文末尾止"
    # ② 重叠量：相邻块共享部分不超过上限（10% of limit = 10 字符）
    for prev, cur in zip(chunks, chunks[1:]):
        shared = _overlap_len(prev, cur)
        assert shared <= 100 // 10, f"相邻块重叠 {shared} 字符超出上限"
    # 重叠必须真的发生（否则「带重叠」这半边整个缺失也测不出来）
    assert any(_overlap_len(p, c) > 0 for p, c in zip(chunks, chunks[1:])), "句末可对齐时应产生重叠"


def test_chunk_unpunctuated_long_text_still_bounded():
    """无标点超长串：必须仍能切出多块、每块不超限且不丢字符（保底按长度切）。"""
    from app.search.chunking import chunk_text
    text = "甲" * 1000
    chunks = chunk_text(text, limit=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks), "保底切片也不得超过上限"
    covered = "".join(chunks)
    assert all(c in covered for c in set(text)), "有字符凭空消失"


def test_chunk_limit_subtracts_prefix_budget():
    """**R3**：生效上限 = CHUNK_CHAR_LIMIT − `build_embed_text` 前缀长度。

    漏掉预留时，末块的实际模型输入 = 512 + 前缀长度，超出模型上限被**静默截断**——
    正是本批「长条文尾部要可召回」的靶心。故每块的最终输入长度必须 ≤ 512 字符，
    且前缀越长、同样正文切出的块越多（预算确实被扣小了）。
    """
    from app.search.chunking import build_embed_chunks
    content = "。".join(f"第{i}句内容" for i in range(400)) + "。"
    texts_short = build_embed_chunks(content, code="GB 1", spec_title="短")
    texts_long = build_embed_chunks(content, code="GB 1", spec_title="某" * 200)

    assert all(len(t) <= CHUNK_CHAR_LIMIT for t in texts_short), "单块输入超出字符上限"
    assert all(len(t) <= CHUNK_CHAR_LIMIT for t in texts_long), "前缀未被计入预算（末块输入超限）"
    assert len(texts_long) > len(texts_short), "前缀变长后块数未增加 → 预算没扣"


def test_chunk_then_embed_text_matches_direct_call():
    """短条文（单块）经切块路径写出的文本必须与直接调用 build_embed_text 完全一致。

    否则导入路径与旧数据/其他调用点会写出两套向量文本（同一条文两个向量）。
    """
    from app.search.chunking import build_embed_chunks
    from app.search.embed_text import build_embed_text
    args = ("GB 50010", "混凝土规范", "5.1.1", "模板", "混凝土保护层厚度应符合规定。", "5 结构")
    assert build_embed_chunks(args[4], code=args[0], spec_title=args[1], clause_no=args[2],
                              clause_title=args[3], section_path=args[5]) == [build_embed_text(*args)]


def test_write_paths_share_one_reservation_helper():
    """前缀预留只允许有一处实现：各写入路径**共用** `build_embed_chunks`。

    各算一套预留会静默分叉（一条超长条文经导入/编辑/重建写出的块数/文本不同）。
    含条文编辑重索引（spec_routes）——U7 把编辑路径也收口到共享入口。
    """
    for rel in ("app/routes/import_routes.py", "app/routes/maintenance_routes.py",
                "app/routes/spec_routes.py", "scripts/reindex_vectors.py"):
        src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "build_embed_chunks" in src, f"{rel} 未走共享的切块/预留入口 build_embed_chunks"


# ═══════════════════════════════════════════
# 写入路径：一条条文 N 行（chunk_index 递增），短条文仍单行 0
# ═══════════════════════════════════════════

_LONG_MD = ("1.0.1 短条文内容。\n"
            "2.0.1 本条文内容很长" + "。" + "长正文甲乙丙丁" * 200 + "。\n")


class _RecordTable:
    """记录 add 进来的整批 records（不做任何 IO）"""

    def __init__(self, sink: list[dict]):
        self._sink = sink

    def add(self, records):
        self._sink.extend(records)


class _RecordVS:
    def __init__(self, sink: list[dict]):
        self._sink = sink

    def _table_exists(self):
        return True

    def _get_table(self):
        return _RecordTable(self._sink)

    def optimize(self):
        """导入收尾会压实：替身只记录 add，压实视为成功（不写入任何记录）"""
        return True


def _run_import_records(isolated_paths, monkeypatch, md_text: str) -> list[dict]:
    """跑真实导入链路（只把向量表换成记录替身），返回写入的向量记录"""
    from app.database import get_db, init_db
    from app.routes import import_routes as ir
    init_db()
    records: list[dict] = []
    monkeypatch.setattr(ir, "VectorStore", lambda: _RecordVS(records))
    # get_model() 也要替身：导入链路会 `if get_model() is None: raise`，只替 embed_texts
    # 的话这里仍会真实加载 SentenceTransformer（约 29 秒），且在**无模型的机器**上
    # 返回 None ⇒ 抛错被向量阶段的 except 吞掉 ⇒ 空 sink ⇒ 下面的断言全部失效（R21）。
    monkeypatch.setattr("app.ai.embedding.get_model", lambda: object())
    monkeypatch.setattr("app.ai.embedding.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    ir.progress_store["chunk001"] = {"status": "processing", "progress": 0, "owner": "t"}
    try:
        ir._process_import_phase2("chunk001", md_text, "切块测试规范", "GB/T 77777-2020",
                                  str(isolated_paths / "f.md"), "hash_chunk", "现行", "")
    finally:
        ir.progress_store.pop("chunk001", None)
    with get_db() as conn:
        ids = {r["clause_no"]: r["id"]
               for r in conn.execute("SELECT id, clause_no FROM clauses")}
    return [dict(r, clause_no=next(k for k, v in ids.items() if v == r["clause_id"]))
            for r in records]


def test_import_writes_one_row_per_chunk_with_increasing_index(isolated_paths, monkeypatch):
    """超长条文按块写多行（chunk_index 0..N-1），短条文保持单行 chunk_index=0。"""
    rows = _run_import_records(isolated_paths, monkeypatch, _LONG_MD)
    by_clause: dict[str, list[dict]] = {}
    for r in rows:
        by_clause.setdefault(r["clause_no"], []).append(r)

    assert set(by_clause) == {"1.0.1", "2.0.1"}, f"条文集合不符: {set(by_clause)}"
    short, long_ = by_clause["1.0.1"], by_clause["2.0.1"]

    assert [r["chunk_index"] for r in short] == [0], "短条文应单行且 chunk_index=0"
    assert [r["chunk_index"] for r in long_] == list(range(len(long_))), \
        f"chunk_index 必须从 0 连续递增: {[r['chunk_index'] for r in long_]}"
    assert len(long_) > 1, "超长条文未切块"
    assert all(len(r["text"]) <= CHUNK_CHAR_LIMIT for r in rows), "存在超出字符上限的向量输入"
    assert all(r["text"].startswith("GB/T 77777-2020 切块测试规范 [") for r in rows), \
        "每块文本都必须带完整前缀（否则块与条文对不上）"
