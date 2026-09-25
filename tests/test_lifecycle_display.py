"""三处状态标签与替代提示语渲染测试"""
from app.database import get_db, init_db


def _setup_with_replaced(conn):
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
        ("GB 50010-2011", "旧混凝土规范", "废止"),
    )
    old_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO specifications (code, title, status) VALUES (?, ?, ?)",
        ("GB 50010-2015", "混凝土结构工程施工质量验收规范", "现行"),
    )
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE specifications SET replace_by_spec_id = ? WHERE id = ?",
        (new_id, old_id),
    )
    conn.execute(
        "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?, ?, ?, ?)",
        (old_id, "1.0.1", "旧条文", "旧条文内容。"),
    )
    return old_id


def test_clause_detail_shows_replacement_notice(auth_client, monkeypatch, tmp_path):
    """详情弹窗底部显示「已废止 → 请查阅新版规范：新编号 新名称」

    文案于 2026-09-25 按用户要求统一：原「本规范已被《X》替代，请以新规范的规定为准」
    与「本规范已废止，请查阅新版规范」两个分支合并为同一句式，并把新版规范编号
    与名称一并给出（详情见 partials/clause_detail.html 的三级兜底注释）。
    """
    db_path = tmp_path / "ld1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        old_id = _setup_with_replaced(conn)
    resp = auth_client.get(f"/clause/{old_id}")
    assert "本规范已废止，请查阅新版规范：GB 50010-2015 混凝土结构工程施工质量验收规范" in resp.text


def test_clause_detail_shows_status_tag(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ld2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        old_id = _setup_with_replaced(conn)
    resp = auth_client.get(f"/clause/{old_id}")
    assert "废止" in resp.text


def test_search_results_show_obsolete_tag(auth_client, monkeypatch, tmp_path):
    """检索结果列表来自废止规范 → 显示「已废止」标识"""
    db_path = tmp_path / "ld3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        old_id = _setup_with_replaced(conn)
    resp = auth_client.get("/search?all=1&status_filter=")
    assert "已废止" in resp.text
