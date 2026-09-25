"""导入流程的向量失败不得静默

背景：导入时若向量库连接失败或 embedding 计算失败，原实现分别「静默 pass」与
「只更新内存进度 message」——进度条结束后用户看不到任何痕迹，条文却一条向量都没写。
事后唯一症状是维护页的「缺失向量索引」，且无从追查原因（system_logs 无记录）。
本文件锁定：两条失败路径都必须在 system_logs 留下 WARN。
"""
from app.database import get_db, init_db


def _setup(monkeypatch, tmp_path, name):
    db_path = tmp_path / f"{name}.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / f"out_{name}"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / f"up_{name}"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH",
                        str(tmp_path / f"lance_{name}"))
    init_db()


def _warn_actions():
    with get_db() as conn:
        return [r["action"] for r in conn.execute(
            "SELECT action FROM system_logs WHERE level='WARN'").fetchall()]


def test_import_vector_store_failure_logs_warning(monkeypatch, tmp_path):
    """向量库连接失败：整批条文都不会有向量，必须留 WARN"""
    _setup(monkeypatch, tmp_path, "vw1")
    import app.routes.import_routes as ir
    ir.progress_store["t1"] = {"status": "processing", "progress": 0}

    def _boom(*args, **kwargs):
        raise RuntimeError("lance connect failed")

    monkeypatch.setattr(ir, "VectorStore", _boom)

    ir._process_import_phase2(
        "t1", "# 第1章\n5.1.1 条文内容测试。\n", "混凝土规范", "GBT 50010-2010",
        str(tmp_path / "f.md"), "hash_vw1",
    )

    warns = _warn_actions()
    assert any("向量" in a for a in warns), f"向量库失败被静默吞掉，WARN 记录: {warns}"


def test_import_embedding_failure_logs_warning(monkeypatch, tmp_path):
    """embedding 计算失败：同样只写内存进度、不留日志 → 必须补 WARN"""
    _setup(monkeypatch, tmp_path, "vw2")
    import app.routes.import_routes as ir
    ir.progress_store["t2"] = {"status": "processing", "progress": 0}

    class _FakeVS:
        """只用于让 vs is not None 成立；embedding 阶段先失败，不会走到建表"""

    monkeypatch.setattr(ir, "VectorStore", lambda: _FakeVS())

    def _boom(texts):
        raise RuntimeError("embedding model missing")

    monkeypatch.setattr("app.ai.embedding.embed_texts", _boom)

    ir._process_import_phase2(
        "t2", "# 第1章\n5.1.1 条文内容测试。\n", "混凝土规范", "GBT 50010-2011",
        str(tmp_path / "f2.md"), "hash_vw2",
    )

    warns = _warn_actions()
    assert any("向量" in a for a in warns), f"embedding 失败被静默吞掉，WARN 记录: {warns}"
