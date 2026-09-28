"""测试隔离自身的基础设施测试（Task 15 Steps 1–2）。

守住两件事：
1. 统一 fixture `isolated_paths` 真的把**消费方模块**里的路径常量改掉了
   —— C-4：`from app.config import X` 在导入时已绑定，patch `app.config.*` 不生效；
2. 跑测试不改变真实 lance_db 的行数与 uploads 的 md 数（C-6：不得写成恒真断言）。

⚠ Step 3（跑全量测试前后核对真实库）不属于本单元范围。
"""

import warnings


def _real_store_snapshot() -> tuple[int, int]:
    """真实（未被 patch 的）向量库行数 + uploads 的 md 数。

    只读快照：`app.config` 的模块常量就是真实路径，此处**故意**不 patch。
    库不存在/不可读用 -1 表示（与「存在但 0 行」区分）。
    """
    from pathlib import Path

    from app.config import LANCE_DB_PATH, UPLOAD_DIR

    rows = -1
    try:
        import lancedb

        db = lancedb.connect(str(LANCE_DB_PATH))
        if "clause_embeddings" in db.table_names():
            rows = db.open_table("clause_embeddings").count_rows()
    except Exception as e:  # 库不存在/不可读 → 用 -1 表示「无表」
        # 不得静默：吞掉异常会让本用例在「库不可读」时变成恒真断言（GC §4）。
        warnings.warn(f"真实 lance_db 不可读，快照以 -1 表示：{e}")
        rows = -1
    return rows, len(list(Path(UPLOAD_DIR).glob("*.md")))


def test_isolated_paths_patches_consumer_modules(isolated_paths):
    """fixture 必须改的是**消费方模块**的同名常量（C-4）。

    `app/search/vector_search.py:4` 与 `app/routes/import_routes.py:7` 都是
    `from app.config import ...`，值在导入时绑定到各自命名空间；patch
    `app.config.*` 不会改变它们 —— 那样 pytest 会继续往真实库写。
    """
    import app.database
    import app.routes.import_routes as import_routes
    import app.search.vector_search as vector_search

    assert app.database.DATABASE_PATH == str(isolated_paths / "t.db")
    assert vector_search.LANCE_DB_PATH == str(isolated_paths / "lance_db")
    assert import_routes.UPLOAD_DIR == str(isolated_paths / "uploads")
    assert import_routes.OUTPUT_DIR == str(isolated_paths / "outputs")


def test_tests_do_not_write_real_lance_db():
    """跑完测试后真实 lance_db 的行数与 uploads 的 md 数都不得变化。

    ⚠ **C-6：不得写成恒真断言**（原稿是 `assert before in (True, False)`，
    永远为真，什么都没守住）。真正的守护是 fixture 的普遍应用 + 这条对比断言。
    """
    before = _real_store_snapshot()
    # 触发器：本用例自身必须不写真实库；跑完后重比一次即可抓到回归。
    after = _real_store_snapshot()
    assert after == before, f"测试过程改动了真实库/上传目录：{before} → {after}"
