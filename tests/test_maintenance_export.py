"""导出/备份端点测试"""
import json
from app.database import init_db, get_db
from tests.conftest import setup_search_data


def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "me.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    monkeypatch.setattr("app.config.BACKUP_DIR", str(tmp_path / "backups"))
    init_db()


def test_backup_creates_sqlite_file(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)
    resp = auth_client.post("/maintenance/backup")
    assert resp.status_code == 200
    backup_files = list((tmp_path / "backups").glob("*.db"))
    assert len(backup_files) == 1
    assert backup_files[0].stat().st_size > 0


def test_export_rules_returns_json(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        conn.execute(
            """INSERT INTO classification_rules (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
               VALUES ('dim4', 'specialty', '钢筋', 'keyword', 1, 0.6, 1)"""
        )
    resp = auth_client.get("/maintenance/export/rules")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert len(data) == 1 and data[0]["pattern"] == "钢筋"


def test_export_review_queue_json(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with get_db() as conn:
        setup_search_data(conn)
        clause = conn.execute("SELECT id FROM clauses LIMIT 1").fetchone()
        conn.execute(
            """INSERT INTO classification_queue (clause_id, dimension, status, ai_label, ai_confidence)
               VALUES (?, 'dim4', 'review', '结构专业', 0.82)""",
            (clause["id"],),
        )
    resp = auth_client.get("/maintenance/export/review-queue")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ai_label"] == "结构专业"
    assert "content" in data[0]  # 含条文内容
