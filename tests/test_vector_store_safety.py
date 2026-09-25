"""VectorStore 删除动作的作用域边界

回归背景（2026-09-25 实测复现）：
`clear_all()` 曾用局部 `from app.config import LANCE_DB_PATH` 重新取路径，
与 `self.db` 的连接来源不是同一个名字。测试 patch 的是模块级
`app.search.vector_search.LANCE_DB_PATH`，于是 `self.db` 连临时目录、
`_table_exists()` 为 False，而 `rmtree` 却落在 `app.config` 的真值
（开发机真实向量库 `lance_db/`）上——每跑一次涉及 `batch_index` 的用例，
真实向量表就被静默删掉一次，测试仍显示 passed，且不留任何日志。

本文件锁定三条底线：
1. 正常：本实例所连库的 `clause_embeddings.lance` 目录确实被清掉（重建能力不被守卫误伤）；
2. 边界：与本实例无关的另一个库目录绝不被碰；
3. 异常：目标目录不存在时不抛异常（幂等）。
"""
import numpy as np

from app.search.vector_search import VectorStore

TABLE_DIR = "clause_embeddings.lance"


def _make_table_dir(root):
    """在 root 下造一个假表目录 + 哨兵文件，返回目录路径"""
    d = root / TABLE_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "_SENTINEL.txt").write_text("真库数据", encoding="utf-8")
    return d


def test_clear_all_removes_own_table_dir(monkeypatch, tmp_path):
    """正常：实例所连库下的表目录应被清掉（守卫不得误伤重建）"""
    conn_db = tmp_path / "conn_lance"
    conn_db.mkdir()
    own_dir = _make_table_dir(conn_db)
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(conn_db))

    VectorStore().clear_all()

    assert not own_dir.exists(), "clear_all 未清掉本实例库中的表目录，重建会残留旧数据"


def test_clear_all_never_touches_other_db(monkeypatch, tmp_path):
    """边界：config 指向的库与实例所连的库不同时，前者绝不能被删

    复刻原缺陷场景——两个路径来源不一致。旧实现会删掉 other_db，
    新实现只认 self.db.uri，故 other_db 必须原封不动。
    """
    other_db = tmp_path / "real_lance"      # 模拟开发机真实向量库
    other_db.mkdir()
    sentinel_dir = _make_table_dir(other_db)

    conn_db = tmp_path / "conn_lance"       # 实例实际连接的（隔离）库
    conn_db.mkdir()

    monkeypatch.setattr("app.config.LANCE_DB_PATH", str(other_db))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(conn_db))

    VectorStore().clear_all()

    assert sentinel_dir.exists(), "clear_all 删除了本实例未连接的库目录"
    assert (sentinel_dir / "_SENTINEL.txt").exists(), "哨兵数据被删除"


def test_batch_index_never_touches_other_db(monkeypatch, tmp_path):
    """边界：重建全程（batch_index）同样不得越界到 config 指向的库"""
    other_db = tmp_path / "real_lance"
    other_db.mkdir()
    sentinel_dir = _make_table_dir(other_db)

    conn_db = tmp_path / "conn_lance"
    conn_db.mkdir()

    monkeypatch.setattr("app.config.LANCE_DB_PATH", str(other_db))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(conn_db))
    monkeypatch.setattr(
        "app.search.vector_search.embed_texts",
        lambda texts: [np.zeros(4, dtype=np.float32) for _ in texts],
    )

    clauses = [{"clause_id": i, "spec_id": 1, "text": f"文本{i}", "dim_scores": ""}
               for i in range(1, 6)]
    VectorStore().batch_index(clauses, batch_size=2)

    assert sentinel_dir.exists(), "batch_index 重建过程中删除了别的库目录"
    assert (sentinel_dir / "_SENTINEL.txt").exists()


def test_clear_all_is_idempotent_when_dir_absent(monkeypatch, tmp_path):
    """异常：表目录不存在时静默返回，不抛异常（重复清理安全）"""
    conn_db = tmp_path / "conn_lance"
    conn_db.mkdir()
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(conn_db))

    VectorStore().clear_all()   # 首次：无表无目录
    VectorStore().clear_all()   # 再次：幂等

    assert not (conn_db / TABLE_DIR).exists()
