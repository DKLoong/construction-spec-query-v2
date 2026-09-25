"""规范状态修改路由测试"""
from app.database import get_db, init_db


def _setup(conn):
    conn.execute("INSERT INTO specifications (code, title) VALUES (?, ?)", ("GB 50204", "混凝土规范"))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_update_spec_status(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss1.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "废止"})
    assert resp.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT status FROM specifications WHERE id = ?", (sid,)).fetchone()
        assert row["status"] == "废止"


def test_update_spec_status_invalid_value_rejected(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "无效状态"})
    assert resp.status_code == 400


def test_specs_table_shows_status_column(auth_client, monkeypatch, tmp_path):
    db_path = tmp_path / "ss3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
        conn.execute("UPDATE specifications SET status = '废止' WHERE id = ?", (sid,))
    resp = auth_client.get("/specs/list")
    assert "状态" in resp.text
    assert "废止" in resp.text


# ═══════════════════════════════════════════
# 状态切换确认（T4）：模板给足回滚所需信息 + 端点不再返回死代码
# ═══════════════════════════════════════════

def test_status_select_carries_previous_value(auth_client, monkeypatch, tmp_path):
    """下拉框须带原值 data-prev，并在变更时走 specStatusChange() 弹确认

    取消时要把下拉框拨回修改前的值，前端必须知道原值——它只能由模板下发。
    """
    db_path = tmp_path / "ss4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
        conn.execute("UPDATE specifications SET status = '废止' WHERE id = ?", (sid,))
    html = auth_client.get("/specs/list").text

    assert f'data-prev="废止"' in html, "下拉框缺少原值（取消时无法回滚）"
    assert "specStatusChange(this)" in html, "变更未走 specStatusChange（不会弹确认框）"


def test_status_endpoint_returns_json_and_no_dead_oob(auth_client, monkeypatch, tmp_path):
    """端点返回 JSON；不再返回 hx-swap-oob 片段

    该片段是死代码：前端一直用 fetch 提交，htmx 从未处理过它，因而从未生效过。
    留着会误导下一个维护者以为「状态标签由 OOB 更新」。
    """
    db_path = tmp_path / "ss5.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    init_db()
    with get_db() as conn:
        sid = _setup(conn)
    resp = auth_client.put(f"/specs/{sid}/status", data={"status": "废止"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "废止"}
    assert "hx-swap-oob" not in resp.text
