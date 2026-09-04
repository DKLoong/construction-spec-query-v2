"""词库管理路由测试（承接原同义词路由测试覆盖：页面/auth、CRUD、toggle、
行内编辑、synonym/alias 同 canonical 并立 400、confusable 多对共存、CSV 导入报告、kind 分支）"""
import pytest


def test_lexicon_page_and_create(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l.db"))
    init_db()
    resp = auth_client.get("/lexicon")
    assert resp.status_code == 200 and "词库" in resp.text
    r2 = auth_client.post("/lexicon/create", data={"kind": "alias", "canonical": "水灰比", "variants": "W/C,水胶比", "distinguish": ""})
    assert r2.status_code == 200 and "已添加" in r2.text
    r3 = auth_client.post("/lexicon/create", data={"kind": "alias", "canonical": "水灰比", "variants": "W/C"})
    assert r3.status_code == 400  # 同 kind canonical 并立 → 拒绝
    # confusable 互含子串拒绝
    r4 = auth_client.post("/lexicon/create", data={"kind": "confusable", "canonical": "沉降", "variants": "差异沉降", "distinguish": "范围不同"})
    assert r4.status_code == 400
    assert "子串" in r4.text


def test_lexicon_toggle_edit_delete_and_filter(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l2.db"))
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','坍落度','塌落度')")
        lid = cur.lastrowid
    assert auth_client.post(f"/lexicon/{lid}/toggle").status_code == 200
    auth_client.post(f"/lexicon/{lid}/edit", data={"canonical": "坍落度", "variants": "塌落度,落度"})
    with get_db() as conn:
        row = conn.execute("SELECT is_active, variants FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
        assert row["is_active"] == 0 and "落度" in row["variants"]
    assert auth_client.delete(f"/lexicon/{lid}").status_code == 200


def test_lexicon_csv_import(auth_client, monkeypatch, tmp_path):
    from app.database import init_db, get_db, DATABASE_PATH
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l3.db"))
    init_db()
    csv_text = ("kind,canonical,variants,distinguish,note\n"
                "alias,防水卷材,卷材,,seed\n"
                "confusable,圈梁,构造柱,竖向构件不同,\n"
                "alias,坍落度,塌落度,,dup\n"
                "alias,坍落度,塌落度,,dup\n"
                ",,bad,,extra\n")
    resp = auth_client.post("/lexicon/import",
                            files={"file": ("seed.csv", csv_text.encode("utf-8"), "text/csv")})
    assert resp.status_code == 200
    body = resp.text
    assert "成功" in body and "跳过" in body and "失败" in body


def test_lexicon_requires_auth(client):
    """未登录不能访问词库管理页"""
    resp = client.get("/lexicon", follow_redirects=False)
    assert resp.status_code == 302


def test_lexicon_list_returns_html(auth_client, monkeypatch, tmp_path):
    """词库列表返回 HTML 片段（含预置 alias 种子）"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_list.db"))
    from app.database import init_db
    init_db()
    resp = auth_client.get("/lexicon/list?kind=alias")
    assert resp.status_code == 200
    assert "混凝土" in resp.text
    assert "砼" in resp.text


def test_lexicon_confusable_multi_pairs_coexist(auth_client, monkeypatch, tmp_path):
    """confusable 同 canonical 多对可共存（A↔B、A↔C 不触发 synonym/alias 组唯一约束）"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_conf.db"))
    from app.database import init_db
    init_db()
    r1 = auth_client.post("/lexicon/create", data={
        "kind": "confusable", "canonical": "圈梁", "variants": "构造柱", "distinguish": "竖向构件不同"})
    assert r1.status_code == 200
    r2 = auth_client.post("/lexicon/create", data={
        "kind": "confusable", "canonical": "圈梁", "variants": "地梁", "distinguish": "位置不同"})
    assert r2.status_code == 200


def test_lexicon_create_invalid_kind(auth_client, monkeypatch, tmp_path):
    """kind 非法 → 400（kind 分支校验）"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_kind.db"))
    from app.database import init_db
    init_db()
    resp = auth_client.post("/lexicon/create", data={"kind": "bogus", "canonical": "X", "variants": "Y"})
    assert resp.status_code == 400
    assert "不合法" in resp.text


def test_lexicon_edit_returns_single_row(auth_client, monkeypatch, tmp_path):
    """行内编辑保存返回单行 partial（非整表），与 closest tr outerHTML 契约匹配"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_edit.db"))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants) VALUES ('alias','水灰比','W/C')")
        lid = cur.lastrowid
    resp = auth_client.post(f"/lexicon/{lid}/edit", data={"canonical": "水灰比", "variants": "W/C,水胶比"})
    assert resp.status_code == 200
    body = resp.text
    assert "<tr" in body and "水胶比" in body
    assert "<thead>" not in body and "<table" not in body  # 单行 partial，非整表
    with get_db() as conn:
        row = conn.execute("SELECT variants FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
        assert row["variants"] == "W/C,水胶比"


def test_lexicon_edit_confusable_returns_distinguish_column(auth_client, monkeypatch, tmp_path):
    """confusable 行内编辑返回行含区分说明列（lexicon_row.html 按 kind 渲染第三列）"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_editc.db"))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO lexicon_entries(kind,canonical,variants,distinguish) "
            "VALUES ('confusable','圈梁','构造柱','竖向不同')")
        lid = cur.lastrowid
    resp = auth_client.post(f"/lexicon/{lid}/edit", data={
        "canonical": "圈梁", "variants": "构造柱", "distinguish": "竖向构件不同"})
    assert resp.status_code == 200
    body = resp.text
    assert "竖向构件不同" in body
    assert "<thead>" not in body


# ═══════════════════════════════════════════
# final-review fix：CSV 导入组唯一合并 / 编码兜底 / 404 / updated_at
# ═══════════════════════════════════════════

def test_lexicon_csv_import_merges_equiv_variants(auth_client, monkeypatch, tmp_path):
    """CSV 两行同 (alias,混凝土) → 合并 variants 为一行，不破坏组唯一（Important #1）

    覆盖：import 对 kind in EQUIV_KINDS 的行按 canonical 补集合并（不改 is_active），
    store.load_equivalent_groups() 不因同 canonical 第二行冲突而返回空。
    """
    from app.database import init_db, get_db
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_merge.db"))
    init_db()
    csv_text = ("kind,canonical,variants,distinguish,note\n"
                "alias,混凝土,砼,,\n"
                "alias,混凝土,混泥土,,\n")
    resp = auth_client.post("/lexicon/import",
                            files={"file": ("merge.csv", csv_text.encode("utf-8"), "text/csv")})
    assert resp.status_code == 200
    with get_db() as conn:
        rows = conn.execute(
            "SELECT variants FROM lexicon_entries WHERE kind='alias' AND canonical='混凝土'"
        ).fetchall()
        assert len(rows) == 1, "同 (kind,canonical) 必须合并为单行"
        assert "砼" in rows[0]["variants"] and "混泥土" in rows[0]["variants"]
    from app.lexicon import store
    store.invalidate_lexicon_caches()
    groups = store.load_equivalent_groups()
    assert groups, "合并后词表一致性校验通过，load_equivalent_groups 不得因冲突返回空"
    concrete = [g for g in groups if g.kind == "alias" and g.canonical == "混凝土"]
    assert concrete and "砼" in concrete[0].variants and "混泥土" in concrete[0].variants


def test_lexicon_import_invalid_encoding_returns_400(auth_client, monkeypatch, tmp_path):
    """CSV 非 UTF-8 解码失败 → 400（#5）"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_enc.db"))
    from app.database import init_db
    init_db()
    resp = auth_client.post("/lexicon/import",
                            files={"file": ("bad.csv", b"\xff\xfe\xfd", "text/csv")})
    assert resp.status_code == 400
    assert "UTF-8" in resp.text


def test_lexicon_delete_missing_returns_404(auth_client, monkeypatch, tmp_path):
    """删除不存在的 id → 404（#6，与 toggle/edit 一致）"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_del.db"))
    from app.database import init_db
    init_db()
    assert auth_client.delete("/lexicon/999999").status_code == 404


def test_lexicon_write_paths_refresh_updated_at(auth_client, monkeypatch, tmp_path):
    """toggle/edit 写路径刷新 updated_at（#9）"""
    from app import database as _db
    monkeypatch.setattr(_db, "DATABASE_PATH", str(tmp_path / "l_ts.db"))
    from app.database import init_db, get_db
    init_db()
    OLD = "2000-01-01 00:00:00"
    with get_db() as conn:
        c1 = conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants,updated_at) "
                          "VALUES ('alias','坍落度','塌落度',?)", (OLD,))
        lid1 = c1.lastrowid
        c2 = conn.execute("INSERT INTO lexicon_entries(kind,canonical,variants,updated_at) "
                          "VALUES ('alias','水灰比','W/C',?)", (OLD,))
        lid2 = c2.lastrowid
    auth_client.post(f"/lexicon/{lid1}/toggle")
    auth_client.post(f"/lexicon/{lid2}/edit", data={"canonical": "水灰比", "variants": "W/C,水胶比"})
    with get_db() as conn:
        for lid in (lid1, lid2):
            row = conn.execute("SELECT updated_at FROM lexicon_entries WHERE id=?", (lid,)).fetchone()
            assert row["updated_at"] != OLD


def test_lexicon_template_download(auth_client):
    """下载模板返回 CSV 表头 + few-shot 三类示例行"""
    resp = auth_client.get("/lexicon/template.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert resp.text.startswith("kind,canonical,variants,distinguish,note")
    for kw in ("synonym", "alias", "confusable", "坍落度", "圈梁", "构造柱"):
        assert kw in resp.text
