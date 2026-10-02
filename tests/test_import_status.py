"""导入入库时 status 打标与 replace_by 反向联动测试"""
import pytest

from app.database import get_db, init_db
from tests.conftest import seed_import_task


@pytest.fixture(autouse=True)
def _isolate_vector_store(monkeypatch, tmp_path):
    """把**真实**向量库隔离掉：本文件 4 个用例都会走到导入的向量写入。

    ⚠️ 原先只 patch 了 `DATABASE_PATH` / `OUTPUT_DIR` / `UPLOAD_DIR`，漏了向量库 ——
    这是**既有的隔离缺口**，长期休眠：本文件的夹具 md 以 `# 第1章`（无编号）开头，
    T20（Task 17 / 改动⑤）之前它连同其后正文一起被「空栈 flush」丢弃，故没有可索引的
    条文、写入路径根本没跑。T20 保留孤儿文本后夹具多出一条隐藏条文 → 向量写入真的发生
    → 真实 `lance_db` 被写入（实测每跑一次本文件 +4 行，会话级守卫随即变红）。
    patch 消费方模块名（`app.search.vector_search.LANCE_DB_PATH`）是仓库既定口径
    （见 `tests/conftest.py::isolated_paths` 的 C-4 说明）；**不是** patch `app.config.*`。
    """
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_db"))


def test_import_phase2_writes_status(monkeypatch, tmp_path):
    """入库时按表单 status 写入，code 过 normalize"""
    db_path = tmp_path / "is1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    init_db()
    import app.routes.import_routes as ir
    seed_import_task("task1", status="processing", progress=0,
                     md_text="# 第1章\n5.1.1 条文内容测试\n")
    from app.routes.import_routes import _process_import_phase2
    _process_import_phase2(
        "task1", "# 第1章\n5.1.1 条文内容测试\n", "混凝土规范", "GBT 50010-2010",
        str(tmp_path / "f.md"), "hash1",
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT code, status FROM specifications WHERE file_hash = 'hash1'"
        ).fetchone()
    assert row["code"] == "GB/T 50010-2010"   # 归一化
    assert row["status"] == "现行"             # 表单未传 → 默认现行


def test_import_reverse_link_obsolete_spec(monkeypatch, tmp_path):
    """replaced_by_code 命中库中旧规范 → 反写旧规范废止 + 关联"""
    db_path = tmp_path / "is2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
            ("GB 50010-2011", "旧规范", "现行"),
        )
    import app.routes.import_routes as ir
    seed_import_task("task2", status="processing", progress=0,
                     md_text="# 第1章\n5.1.1 新条文内容\n")
    from app.routes.import_routes import _process_import_phase2
    # 导入新规范 GB 50010-2015，界面校核出它替代 GB 50010-2011 → 传 replaced_by_code
    _process_import_phase2(
        "task2", "# 第1章\n5.1.1 新条文内容\n", "混凝土新规范", "GB 50010-2015",
        str(tmp_path / "f2.md"), "hash2", status="现行", replaced_by_code="GB 50010-2011",
    )
    with get_db() as conn:
        new = conn.execute("SELECT id FROM specifications WHERE code = 'GB 50010-2015'").fetchone()
        old = conn.execute(
            "SELECT status, replace_by_spec_id FROM specifications WHERE code = 'GB 50010-2011'"
        ).fetchone()
    assert old["status"] == "废止"
    assert old["replace_by_spec_id"] == new["id"]


def test_import_reverse_link_ignores_self(monkeypatch, tmp_path):
    """同码自我替代：replaced_by_code == 自身 code → 不把自己标废止、关联留空"""
    db_path = tmp_path / "is3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    init_db()
    import app.routes.import_routes as ir
    seed_import_task("task3", status="processing", progress=0,
                     md_text="# 第1章\n5.1.1 新条文内容\n")
    from app.routes.import_routes import _process_import_phase2
    # 同码重导：replaced_by_code 归一化后等于自身 code
    _process_import_phase2(
        "task3", "# 第1章\n5.1.1 新条文内容\n", "混凝土规范", "GB 50010-2015",
        str(tmp_path / "f3.md"), "hash3", status="现行", replaced_by_code="GB 50010-2015",
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT status, replace_by_spec_id FROM specifications WHERE code = 'GB 50010-2015'"
        ).fetchone()
    assert row["status"] == "现行"
    assert row["replace_by_spec_id"] is None


def test_import_obsolete_spec_persists_replaced_by_code(monkeypatch, tmp_path):
    """导入的是一本**废止**规范：校核出的新版编号须落到**自己**身上

    用户反馈的场景：导入废止规范时界面显示了替代它的新版编号，条文详情页却只字未提
    —— 因为该编号此前只被用来反查库中记录，查不到就直接丢弃、从未持久化。
    """
    db_path = tmp_path / "is4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    init_db()
    import app.routes.import_routes as ir
    seed_import_task("task4", status="processing", progress=0,
                     md_text="# 第1章\n5.1.1 旧条文内容\n")
    from app.routes.import_routes import _process_import_phase2
    _process_import_phase2(
        "task4", "# 第1章\n5.1.1 旧条文内容\n", "钢筋机械连接技术规程", "JGJ 107-2010",
        str(tmp_path / "f4.md"), "hash4", status="废止", replaced_by_code="JGJ 107-2024",
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT status, replaced_by_code, replace_by_spec_id FROM specifications "
            "WHERE code = 'JGJ 107-2010'"
        ).fetchone()
    assert row["status"] == "废止"
    assert row["replaced_by_code"] == "JGJ 107-2024", \
        "校核出的新版编号未持久化（详情页将无法给出编号）"
    assert row["replace_by_spec_id"] is None, "新版不在库中，不该凭空关联"
