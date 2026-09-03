"""参数方案 API 测试：meta/profiles CRUD/apply（自定义写键、默认删键回滚）"""
from app.database import init_db, get_db
from app.params import registry


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "params.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    init_db()
    registry.clear_param_cache()


def _set(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     (key, str(value)))


def _get(key):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def test_meta_and_default_profile(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    meta = auth_client.get("/maintenance/params/meta").json()
    assert [g["id"] for g in meta["groups"]] == ["classify", "search", "qa"]
    assert len(meta["params"]) >= 20
    profiles = auth_client.get("/maintenance/params/profiles").json()
    assert profiles[0]["id"] == 0
    assert profiles[0]["name"] == "默认方案" and profiles[0]["is_system"] is True


def test_create_profiles_auto_name(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r1 = auth_client.post("/maintenance/params/profiles",
                          json={"values": {"classify.batch_size": "25"}})
    assert r1.status_code == 200
    p1 = r1.json()
    assert p1["name"] == "方案1" and p1["values"] == {"classify.batch_size": "25"}
    r2 = auth_client.post("/maintenance/params/profiles",
                          json={"name": "调教A", "values": {"search.rrf_k": "90"}})
    assert r2.status_code == 200 and r2.json()["name"] == "调教A"


def test_create_rejects_invalid_or_unknown(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r = auth_client.post("/maintenance/params/profiles",
                         json={"values": {"classify.batch_size": "99999"}})
    assert r.status_code == 400
    r2 = auth_client.post("/maintenance/params/profiles",
                          json={"values": {"nope.key": "1"}})
    assert r2.status_code == 400


def test_update_rename_and_default_readonly(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    p = auth_client.post("/maintenance/params/profiles",
                         json={"values": {"search.vector_top_k": "5"}}).json()
    r = auth_client.put(f"/maintenance/params/profiles/{p['id']}",
                        json={"name": "我的调教", "values": {"search.vector_top_k": "8"}})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "我的调教" and body["values"] == {"search.vector_top_k": "8"}
    # 重名 400：另一方案自动命名「方案1」，再改名冲突
    other = auth_client.post("/maintenance/params/profiles",
                             json={"values": {"search.vector_top_k": "3"}}).json()
    assert other["name"] == "方案1"
    r2 = auth_client.put(f"/maintenance/params/profiles/{p['id']}",
                         json={"name": "方案1"})
    assert r2.status_code == 400
    # 默认方案（虚拟 0）只读
    r3 = auth_client.put("/maintenance/params/profiles/0", json={"name": "xx"})
    assert r3.status_code == 400


def test_apply_custom_then_default_rollback(auth_client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _set("classify.ai_confidence_threshold", "0.9")  # 预设一个既有覆盖
    p = auth_client.post("/maintenance/params/profiles",
                         json={"values": {"classify.batch_size": "25"}}).json()
    ar = auth_client.post(f"/maintenance/params/profiles/{p['id']}/apply")
    assert ar.status_code == 200 and ar.json()["applied"] == p["id"]
    # 自定义应用：先删全组键再写 profile 值 + marker
    assert _get("classify.ai_confidence_threshold") is None
    assert _get("classify.batch_size") == "25"
    assert _get("params.applied_profile_id") == str(p["id"])
    # 应用默认（0）= 删全部注册表键 + 删 marker → 回滚内置默认
    br = auth_client.post("/maintenance/params/profiles/0/apply")
    assert br.status_code == 200 and br.json()["applied"] == 0
    assert _get("classify.batch_size") is None
    assert _get("params.applied_profile_id") is None


def test_delete_profile(auth_client, monkeypatch, tmp_path):
    """删除自定义方案（默认 id=0 拒绝；不存在 404）"""
    _setup(monkeypatch, tmp_path)
    p = auth_client.post("/maintenance/params/profiles",
                         json={"name": "待删", "values": {"search.rrf_k": "90"}}).json()
    pid = p["id"]
    r = auth_client.delete(f"/maintenance/params/profiles/{pid}")
    assert r.status_code == 200
    assert auth_client.get("/maintenance/params/profiles").json() == [
        {"id": 0, "name": "默认方案", "is_system": True, "values": {}}]
    assert auth_client.delete("/maintenance/params/profiles/0").status_code == 400
    assert auth_client.delete("/maintenance/params/profiles/999").status_code == 404


def test_param_operations_logged_with_username(auth_client, monkeypatch, tmp_path):
    """参数方案 新建/应用/回滚 均有审计且操作者=admin"""
    _setup(monkeypatch, tmp_path)
    auth_client.post("/maintenance/params/profiles",
                     json={"values": {"classify.batch_size": "25"}}).json()
    pid = auth_client.get("/maintenance/params/profiles").json()[1]["id"]
    auth_client.post(f"/maintenance/params/profiles/{pid}/apply")
    auth_client.post("/maintenance/params/profiles/0/apply")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT action, username FROM system_logs "
            "WHERE category = 'maintenance' AND action LIKE '%参数方案%' "
            "ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == [
        "新建参数方案", "应用参数方案", "应用默认参数方案"]
    assert all(r["username"] == "admin" for r in rows)
