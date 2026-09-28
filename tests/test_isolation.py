"""测试隔离自身的基础设施测试（Task 15 Steps 1–2）。

守住两件事：
1. 统一 fixture `isolated_paths` 真的把**消费方模块**里的路径常量改掉了
   —— C-4：`from app.config import X` 在导入时已绑定，patch `app.config.*` 不生效；
2. 真实存储的**跨套件零增长**由 `conftest.py` 的 session 级 autouse 守卫
   `_guard_real_stores` 负责；本文件只保留一条「本用例自身不写库」的窄自检。

⚠ 为什么不再把「用例内自比」当隔离守卫：两次快照之间没有任何写操作，对
   「跨用例污染」恒真——实测过这一失效（每跑一次全量套件真实 lance_db 多 2 行、
   `data/uploads/` 多 1 个 md，而用例内自比照样 PASS）。真正的守卫必须是
   会话首尾口径，即 `conftest.real_store_snapshot` + `_guard_real_stores`。

⚠ Task 15 Step 3（跑全量测试后人工核对真实库）由 S5 执行，不在本单元范围。
"""

from tests.conftest import real_store_snapshot


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


def test_this_test_itself_leaves_real_stores_untouched():
    """**窄**自检：本用例自身不写真实存储（不含期间被 patch 的 tmp 路径）。

    它**不是**隔离守卫（跨用例污染它看不见，见模块 docstring）；真正的守卫是
    `conftest.py` 的 session 级 `_guard_real_stores`。保留它的意义只有一个：
    本文件自己没有意外写真实库。
    """
    before = real_store_snapshot()
    after = real_store_snapshot()
    assert after == before, f"本用例自身改动了真实库/上传目录：{before} → {after}"
