"""向量重建脚本的一致性守卫（源码级，不加载模型）

背景（2026-09-19）：`scripts/reindex_vectors.py` 曾内联重复实现 embedding 文本拼接，
绕过了 `build_embed_text`——后果有二：
1. 与导入路径格式漂移（空 title 时单空格 vs 双空格），同一批条文两条路径产出不同向量；
2. `build_embed_text` 接入 `plain_text` 后，重建会把 OCR 标记重新灌回向量。

2026-09-28（批二 U5）起，四条写入路径共用的入口上移为
`app.search.chunking.build_embed_chunks`（内部才调 `build_embed_text`，并统一做
超长条文切块与前缀预算预留）。故这里断言的是**共用入口**——若写回
`build_embed_text`，重建就绕过了切块（长条文尾部永久不可召回）与前缀预留。

本文件用源码断言守住该约束，避免再次漂移。
刻意不 import 脚本本体（其模块级 import 会拉起 embedding 模型）。
"""
from pathlib import Path

_REINDEX = Path(__file__).resolve().parents[1] / "scripts" / "reindex_vectors.py"


def test_reindex_vectors_reuses_shared_embed_chunk_builder():
    """重建脚本必须复用 build_embed_chunks，不得内联拼接文本、也不得跳过切块"""
    src = _REINDEX.read_text(encoding="utf-8")
    assert "build_embed_chunks" in src, "必须调用 build_embed_chunks（切块 + 前缀预留的唯一入口）"
    assert "text_parts" not in src, "不得内联重复实现拼接（会绕过 plain_text 并再次漂移）"


def _load_reindex_module():
    """以独立模块名加载重建脚本本体（不触发 `__main__` 分支的 stdout 包裹）。

    源码级守卫只防「内联拼接/绕过切块」，防不了「查询漏选 c.section_path 却传空串」
    这类静默漂移；下面的功能级用例跑真实的 main() 并读回向量表 text 列来钉住它。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("reindex_vectors_under_test", _REINDEX)
    assert spec is not None, f"无法定位重建脚本模块: {_REINDEX}"
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_reindex_vectors_includes_section_path(tmp_path, monkeypatch):
    """重建后的向量文本必须含面包屑——否则「搜节名」在向量臂失效。

    这是功能级守卫：跑真实的 reindex_vectors.main()（真建表、真写向量、读回 text 列），
    断言重建产物的 text 确实含 section_path 的面包屑。批二的存量向量补面包屑只有
    「全量重建」这一条通道，这里漏了是静默的。
    """
    from app.database import init_db, get_db

    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    # 假嵌入：不加载真实模型（8 维即可满足建表）
    monkeypatch.setattr("app.search.vector_search.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50010', '混凝土规范')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content, section_path)
               VALUES (?, ?, ?, ?, ?)""",
            (spec_id, "5.1.1", "模板", "内容", "5 混凝土分项工程 > 5.1 模板"),
        )

    mod = _load_reindex_module()
    monkeypatch.setattr(mod, "get_model", lambda: object())  # 让 main() 的 None 检查通过
    mod.main()

    from app.search.vector_search import VectorStore
    rows = VectorStore()._get_table().to_arrow().to_pylist()
    assert rows, "重建后向量表应有行"
    assert any("混凝土分项工程" in r["text"] for r in rows), \
        "重建后的向量文本不含面包屑（查询漏选 section_path 或未传第 6 参）"


def test_reindex_vectors_compacts_after_batch_index(tmp_path, monkeypatch, capsys):
    """重建脚本收尾必须压实向量表，且必须在 batch_index **之后**。

    为什么这条最要紧：全量重建是本批唯一一次写满整库的操作，子块 + 10% 重叠使行数
    上升 ~15-18%、每次 add/delete 又各产生一个版本——版本增长在这里最猛（维护页重建
    与导入路径都已接线，漏掉脚本等于在最该压实的那一次不压实）。

    证据形态：跑真实的 `main()`（真建表、真写向量、真压实），只在 `VectorStore` 的
    两个方法上加**记录并透传**的间谍，断言调用顺序。源码断言只防「写法漂移」，
    证伪不了「没被调用」，故不作主证据。
    """
    from app.database import init_db, get_db

    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    monkeypatch.setattr("app.search.vector_search.embed_texts",
                        lambda texts: [[0.0] * 8 for _ in texts])
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('GB 50010', '混凝土规范')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO clauses (spec_id, clause_no, title, content)
               VALUES (?, ?, ?, ?)""",
            (spec_id, "5.1.1", "模板", "内容"),
        )

    from app.search.vector_search import VectorStore
    calls: list[str] = []
    real_batch_index = VectorStore.batch_index
    real_optimize = VectorStore.optimize

    def spy_batch_index(self, *args, **kwargs):
        calls.append("batch_index")
        return real_batch_index(self, *args, **kwargs)

    def spy_optimize(self):
        calls.append("optimize")
        return real_optimize(self)

    monkeypatch.setattr(VectorStore, "batch_index", spy_batch_index)
    monkeypatch.setattr(VectorStore, "optimize", spy_optimize)

    mod = _load_reindex_module()
    monkeypatch.setattr(mod, "get_model", lambda: object())  # 让 main() 的 None 检查通过
    mod.main()

    assert calls == ["batch_index", "optimize"], \
        f"重建脚本未在 batch_index 之后调 optimize: {calls}"
    assert "Compact" in capsys.readouterr().out, "压实这一步应有 stdout 提示（长停顿需可见）"
