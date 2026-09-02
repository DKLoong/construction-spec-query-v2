"""导入入库时 status 打标与 replace_by 反向联动测试"""
from app.database import get_db, init_db


def test_import_phase2_writes_status(monkeypatch, tmp_path):
    """入库时按表单 status 写入，code 过 normalize"""
    db_path = tmp_path / "is1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    init_db()
    import app.routes.import_routes as ir
    ir.progress_store["task1"] = {"status": "processing", "progress": 0}  # phase2 内部 update 依赖
    ir.progress_store["task1"]["md_text"] = "# 第1章\n5.1.1 条文内容测试\n"
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
    ir.progress_store["task2"] = {"status": "processing", "progress": 0}
    ir.progress_store["task2"]["md_text"] = "# 第1章\n5.1.1 新条文内容\n"
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
    ir.progress_store["task3"] = {"status": "processing", "progress": 0}
    ir.progress_store["task3"]["md_text"] = "# 第1章\n5.1.1 新条文内容\n"
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
