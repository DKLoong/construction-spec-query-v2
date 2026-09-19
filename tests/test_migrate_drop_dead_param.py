"""scripts/migrate_drop_rule_auto_enable_conf.py 的迁移测试。"""
import json
import sqlite3

from app.database import init_db
from scripts.migrate_drop_rule_auto_enable_conf import DEAD_KEY, run_migration


def _db(monkeypatch, tmp_path) -> str:
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr("app.database.DATABASE_PATH", db_path)
    init_db()
    return db_path


def _seed(db_path: str) -> None:
    """写入两处残留：settings 覆盖值 + 两个方案（一个含死键、一个不含）。"""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                         (DEAD_KEY, "0.9"))
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                         ("classify.batch_size", "20"))
            conn.execute(
                "INSERT INTO param_profiles (name, is_system, values_json) VALUES (?, 0, ?)",
                ("方案1", json.dumps({DEAD_KEY: "0.9", "classify.batch_size": "20"})))
            conn.execute(
                "INSERT INTO param_profiles (name, is_system, values_json) VALUES (?, 0, ?)",
                ("方案2", json.dumps({"classify.batch_size": "30"})))
    finally:
        conn.close()


def _read(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        settings = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
        profiles = {r["name"]: json.loads(r["values_json"])
                    for r in conn.execute("SELECT name, values_json FROM param_profiles")}
        return {"settings": settings, "profiles": profiles}
    finally:
        conn.close()


def test_migration_removes_dead_key_everywhere(monkeypatch, tmp_path):
    """settings 与 param_profiles 中的死键都被清理，其余键原样保留。"""
    db = _db(monkeypatch, tmp_path)
    _seed(db)

    stats = run_migration(db)
    assert stats == {"settings": 1, "profiles": 1}

    got = _read(db)
    assert DEAD_KEY not in got["settings"]
    assert got["settings"]["classify.batch_size"] == "20"      # 其它键不受影响
    assert DEAD_KEY not in got["profiles"]["方案1"]
    assert got["profiles"]["方案1"]["classify.batch_size"] == "20"
    assert got["profiles"]["方案2"] == {"classify.batch_size": "30"}   # 无死键的方案不动


def test_migration_idempotent(monkeypatch, tmp_path):
    """重复执行不报错、不改变已清理的状态。"""
    db = _db(monkeypatch, tmp_path)
    _seed(db)
    run_migration(db)

    stats2 = run_migration(db)
    assert stats2 == {"settings": 0, "profiles": 0}
    got = _read(db)
    assert DEAD_KEY not in got["settings"]
    assert DEAD_KEY not in got["profiles"]["方案1"]


def test_migration_handles_empty_and_null_values_json(monkeypatch, tmp_path):
    """values_json 为空串/NULL/非法 JSON 之外的正常空对象不炸。"""
    db = _db(monkeypatch, tmp_path)
    conn = sqlite3.connect(db)
    try:
        with conn:
            conn.execute("INSERT INTO param_profiles (name, is_system, values_json) "
                         "VALUES ('空方案', 0, '{}')")
    finally:
        conn.close()
    stats = run_migration(db)
    assert stats == {"settings": 0, "profiles": 0}
