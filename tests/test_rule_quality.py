"""规则质量报表测试（三类异常 + 一键处理）"""
from app.database import init_db, get_db
from app.config import RULE_AUTO_ENABLE_RATIO, RULE_AUTO_ENABLE_MIN_HIT


def _seed(conn):
    # 建议启用：is_active=0 且 hit>=5 且正确率>=0.8
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','钢筋','keyword',0,0.6,6,5,0,datetime('now','localtime'))"""
    )
    # 建议停用：is_active=1 且 hit>10 且正确率<0.3
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','废词','keyword',0,0.6,15,3,1,datetime('now','localtime'))"""
    )
    # 僵尸：hit=0 且超 30 天
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','老词','keyword',0,0.6,0,0,1,datetime('now','localtime','-40 days'))"""
    )
    # 正常规则（不应进入任何类）
    conn.execute(
        """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,
           priority, threshold, hit_count, confirmed, is_active, created_at)
           VALUES ('dim4','specialty','混凝土','keyword',0,0.6,20,16,1,datetime('now','localtime'))"""
    )


def test_quality_rows_classify(monkeypatch, tmp_path):
    db_path = tmp_path / "rq.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
        from app.routes.rules_routes import _quality_rows
        q = _quality_rows(conn)
    assert [r["pattern"] for r in q["suggest_enable"]] == ["钢筋"]
    assert [r["pattern"] for r in q["suggest_disable"]] == ["废词"]
    assert [r["pattern"] for r in q["zombie"]] == ["老词"]


def test_batch_action_enable_all(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rq2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
    resp = auth_client.post("/rules/quality/batch", json={"action": "enable_all", "kind": "suggest_enable"})
    assert resp.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT is_active FROM classification_rules WHERE pattern='钢筋'").fetchone()
    assert row["is_active"] == 1


def test_batch_action_delete_all_zombie(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rq3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
    resp = auth_client.post("/rules/quality/batch", json={"action": "delete_all", "kind": "zombie"})
    assert resp.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM classification_rules WHERE pattern='老词'").fetchone()
    assert row[0] == 0


def test_quality_export_json(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "rq4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        _seed(conn)
    resp = auth_client.get("/rules/quality/export")
    assert resp.status_code == 200
    data = resp.json()
    assert {"suggest_enable", "suggest_disable", "zombie"} <= set(data.keys())
