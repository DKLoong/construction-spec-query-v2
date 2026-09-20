"""同 (维度, 关键词) 重复规则清理迁移测试

保留优先级：有标签 > (hit_count + confirmed) 高 > id 小；
被删行的统计须累加进保留行（否则保留行可能因 hit_count=0 被质量报表误判为僵尸规则）。
跨维度同词不是重复，绝不合并。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import migrate_rule_unique_key as m  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """建真实 schema 的库，然后删掉唯一索引以便造出重复行"""
    path = tmp_path / "dedup.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(path))
    from app.database import get_connection, init_db

    init_db()
    c = get_connection()
    c.execute("DROP INDEX IF EXISTS uq_rule_dim_pattern")  # 造重复的前置
    c.commit()
    yield c
    c.close()


def _insert(conn, dimension, pattern, label=None, hit=0, confirmed=0):
    conn.execute(
        "INSERT INTO classification_rules (dimension, sub_field, pattern, match_type,"
        " priority, threshold, hit_count, confirmed, is_active, label)"
        " VALUES (?, 'x', ?, 'keyword', 0, 0.5, ?, ?, 1, ?)",
        (dimension, pattern, hit, confirmed, label),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _rows(conn, pattern):
    return conn.execute(
        "SELECT * FROM classification_rules WHERE pattern = ? ORDER BY id", (pattern,)
    ).fetchall()


def test_dry_run_reports_without_changing(conn):
    """默认只报告，不改数据"""
    a = _insert(conn, "dim6", "翻模", label="翻模")
    b = _insert(conn, "dim6", "翻模")
    _insert(conn, "dim6", "翻模")

    report = m.resolve_duplicates(conn, apply=False)
    assert len(report["groups"]) == 1
    g = report["groups"][0]
    assert g["pattern"] == "翻模"
    assert g["keep_id"] == a, "有标签的应被选为保留行"
    assert set(g["drop_ids"]) == {b, conn.execute(
        "SELECT MAX(id) FROM classification_rules").fetchone()[0]}
    assert report["deleted"] == 0
    assert len(_rows(conn, "翻模")) == 3, "dry-run 不得删行"


def test_apply_keeps_labeled_and_deletes_others(conn):
    """执行后仅保留有标签那条，其余删除"""
    keep = _insert(conn, "dim6", "翻模", label="翻模")
    _insert(conn, "dim6", "翻模")
    _insert(conn, "dim6", "翻模")

    report = m.resolve_duplicates(conn, apply=True)
    rows = _rows(conn, "翻模")
    assert [r["id"] for r in rows] == [keep]
    assert rows[0]["label"] == "翻模"
    assert report["deleted"] == 2


def test_apply_merges_stats_into_keeper(conn):
    """被删行的 hit/confirmed 须累加进保留行，不丢命中历史"""
    keep = _insert(conn, "dim6", "翻模", label="翻模", hit=1, confirmed=1)
    _insert(conn, "dim6", "翻模", hit=3, confirmed=2)
    _insert(conn, "dim6", "翻模", hit=4, confirmed=0)

    m.resolve_duplicates(conn, apply=True)
    r = _rows(conn, "翻模")[0]
    assert r["id"] == keep
    assert r["hit_count"] == 1 + 3 + 4
    assert r["confirmed"] == 1 + 2 + 0


def test_keep_priority_prefers_higher_hits(conn):
    """都无标签时，命中数高者优先保留"""
    _insert(conn, "dim6", "翻模", hit=0)
    high = _insert(conn, "dim6", "翻模", hit=5)
    _insert(conn, "dim6", "翻模", hit=2)

    report = m.resolve_duplicates(conn, apply=True)
    assert report["groups"][0]["keep_id"] == high
    assert [r["id"] for r in _rows(conn, "翻模")] == [high]


def test_keep_priority_tiebreak_lowest_id(conn):
    """命中数相同时取最早创建那条（id 小）

    注意须独立用例：上一条若先跑完 resolve_duplicates，唯一索引已建立，
    再插重复行会被索引挡住（IntegrityError）。
    """
    first = _insert(conn, "dim6", "爬模", hit=1)
    _insert(conn, "dim6", "爬模", hit=1)

    report = m.resolve_duplicates(conn, apply=True)
    assert report["groups"][0]["keep_id"] == first


def test_apply_is_idempotent(conn):
    """重复执行：第二次无可清理项"""
    _insert(conn, "dim6", "翻模", label="翻模")
    _insert(conn, "dim6", "翻模")

    m.resolve_duplicates(conn, apply=True)
    second = m.resolve_duplicates(conn, apply=True)
    assert second["groups"] == []
    assert second["deleted"] == 0


def test_apply_creates_unique_index(conn):
    """清理后须补建唯一索引，从此新重复被 DB 层挡住"""
    _insert(conn, "dim6", "翻模", label="翻模")
    _insert(conn, "dim6", "翻模")

    report = m.resolve_duplicates(conn, apply=True)
    assert report["index"] == "已建/已存在"

    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_rule_dim_pattern'")]
    assert names == ["uq_rule_dim_pattern"]
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, "dim6", "翻模")


def test_same_pattern_other_dimension_not_merged(conn):
    """跨维度同词合法（钢筋 在 dim5/dim6 含义不同）→ 不算重复、不得合并"""
    a = _insert(conn, "dim5", "钢筋", label="主体结构")
    b = _insert(conn, "dim6", "钢筋", label="钢筋")

    report = m.resolve_duplicates(conn, apply=True)
    assert report["groups"] == []
    assert {r["id"] for r in _rows(conn, "钢筋")} == {a, b}
    # 唯一索引也须能正常建立（跨维度同词不违反 (dimension, pattern) 唯一）
    assert report["index"] == "已建/已存在"
