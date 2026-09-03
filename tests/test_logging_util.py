"""log_action 日志工具测试"""
from app.database import init_db, get_db


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "log.db"))
    init_db()


def test_log_action_writes_system_logs(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.logging_util import log_action
    log_action("maintenance", "INFO", "健康检查", detail='{"checks":1}', username="admin")
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM system_logs WHERE action = '健康检查'"
        ).fetchone()
    assert row is not None
    assert row["category"] == "maintenance"
    assert row["level"] == "INFO"
    assert row["detail"] == '{"checks":1}'
    assert row["username"] == "admin"


def test_log_action_level_warn(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from app.logging_util import log_action
    log_action("spec", "WARN", "孤立条文", detail="2")
    with get_db() as conn:
        row = conn.execute(
            "SELECT level FROM system_logs WHERE action = '孤立条文'"
        ).fetchone()
    assert row["level"] == "WARN"


def test_json_detail_dict_roundtrip():
    """字典可 JSON 序列化 → 保持中文、键序、结构不变"""
    from app.logging_util import json_detail
    out = json_detail({"category": "spec", "clause_count": 3, "msg": "中文条文"})
    assert out == '{"category": "spec", "clause_count": 3, "msg": "中文条文"}'


def test_json_detail_unserializable_fallback_str():
    """含 set/自定义对象等不可序列化内容 → 回退 str()，绝不抛异常"""
    from app.logging_util import json_detail
    out = json_detail({"tags": {1, 2}})
    assert isinstance(out, str)
    assert "tags" in out
    class _Dummy:
        pass
    out2 = json_detail(_Dummy())
    assert isinstance(out2, str)
    assert out2 != "null"


def test_json_detail_none():
    """None → 'null'（与 json.dumps 语义一致）"""
    from app.logging_util import json_detail
    assert json_detail(None) == "null"
