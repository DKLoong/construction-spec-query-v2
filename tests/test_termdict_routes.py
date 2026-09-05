import pytest
from app.database import get_db


def _pre(conn):
    conn.execute("INSERT INTO term_labels (dimension,label,canonical,source) VALUES ('dim6','钢索','钢索','manual')")


def test_page_ok(auth_client):
    resp = auth_client.get("/termdict")
    assert resp.status_code == 200
    assert "术语 / 维度词典" in resp.text


def test_create_new(auth_client):
    resp = auth_client.post("/termdict/create", data={
        "dimension": "dim6", "label": "索具", "canonical": "索具",
        "aliases": "", "note": ""})
    assert resp.status_code == 200 and "新权威标签已添加" in resp.text
    with get_db() as conn:
        r = conn.execute("SELECT canonical, source FROM term_labels WHERE label='索具'").fetchone()
    assert r["canonical"] == "索具" and r["source"] == "manual"


def test_create_existing_label_merges_word(auth_client):
    with get_db() as conn:
        _pre(conn)
    resp = auth_client.post("/termdict/create", data={
        "dimension": "dim6", "label": "钢索", "canonical": "悬索",
        "aliases": "", "note": ""})
    assert resp.status_code == 200 and "已并入" in resp.text
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM term_labels WHERE dimension='dim6' AND label='钢索'").fetchone()["n"]
        a = conn.execute("SELECT aliases FROM term_labels WHERE label='钢索'").fetchone()["aliases"]
    assert n == 1 and "悬索" in a


def test_create_inactive_label_returns_hint_not_merged(auth_client):
    with get_db() as conn:
        _pre(conn)
        conn.execute("UPDATE term_labels SET is_active = 0 WHERE label='钢索'")
    resp = auth_client.post("/termdict/create", data={
        "dimension": "dim6", "label": "钢索", "canonical": "悬索", "aliases": "", "note": ""})
    assert resp.status_code == 400 and "已停用" in resp.text
    with get_db() as conn:
        r = conn.execute("SELECT is_active, aliases FROM term_labels WHERE label='钢索'").fetchone()
    assert r["is_active"] == 0 and "悬索" not in (r["aliases"] or "")


def test_create_word_owned_by_other_label_rejected(auth_client):
    with get_db() as conn:
        _pre(conn)                       # 钢索 已归属 dim6「钢索」
    resp = auth_client.post("/termdict/create", data={
        "dimension": "dim6", "label": "悬索", "canonical": "钢索", "aliases": "", "note": ""})
    assert resp.status_code == 400 and "已属于" in resp.text


def test_create_invalid_dimension_rejected(auth_client):
    resp = auth_client.post("/termdict/create", data={
        "dimension": "dim1", "label": "x", "canonical": "x"})
    assert resp.status_code == 400 and "维度" in resp.text


def test_toggle_and_delete(auth_client):
    with get_db() as conn:
        _pre(conn)
        tid = conn.execute("SELECT id FROM term_labels WHERE label='钢索'").fetchone()["id"]
    resp = auth_client.post(f"/termdict/{tid}/toggle")
    assert resp.status_code == 200
    with get_db() as conn:
        act = conn.execute("SELECT is_active FROM term_labels WHERE id=?", (tid,)).fetchone()["is_active"]
    assert act == 0
    resp = auth_client.delete(f"/termdict/{tid}")
    assert resp.status_code == 200
    with get_db() as conn:
        gone = conn.execute("SELECT COUNT(*) n FROM term_labels WHERE id=?", (tid,)).fetchone()["n"]
    assert gone == 0


def test_edit_updates_fields(auth_client):
    with get_db() as conn:
        _pre(conn)
        tid = conn.execute("SELECT id FROM term_labels WHERE label='钢索'").fetchone()["id"]
    resp = auth_client.post(f"/termdict/{tid}/edit", data={
        "canonical": "缆索", "aliases": "钢丝绳", "note": "桥用"})
    assert resp.status_code == 200
    with get_db() as conn:
        r = conn.execute("SELECT canonical, aliases, note FROM term_labels WHERE id=?", (tid,)).fetchone()
    assert r["canonical"] == "缆索" and "钢丝绳" in r["aliases"] and r["note"] == "桥用"
