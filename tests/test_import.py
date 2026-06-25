def test_import_page_protected(client):
    resp = client.get("/import", follow_redirects=False)
    assert resp.status_code in (302, 303, 401)


def test_upload_no_file_authenticated(auth_client):
    resp = auth_client.post("/import/upload")
    assert resp.status_code in (400, 422)


def test_upload_markdown(auth_client, monkeypatch, tmp_path):
    """测试上传 MD 文件导入流程"""
    db_path = tmp_path / "test_import.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    # 创建测试用的 MD 文件
    md_content = """# 测试规范

## 1 总则

### 1.1 一般规定

混凝土施工应满足设计要求。

### 1.2 材料

钢筋进场时按标准检验。
"""
    import io
    md_file = io.BytesIO(md_content.encode("utf-8"))
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("test.md", md_file, "text/markdown")},
        data={"code": "GB 99999", "title": "测试规范"},
    )
    # Should return 200 with progress HTML
    assert resp.status_code == 200
    assert "处理中" in resp.text or "import-status" in resp.text


def test_upload_duplicate_rejected(auth_client, monkeypatch, tmp_path):
    """重复上传相同文件应被拒绝"""
    db_path = tmp_path / "test_dup.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()

    # 预先插入一条带 file_hash 的规范记录
    import hashlib
    md_content = "# 重复测试\n## 1.1 条文\n内容".encode("utf-8")
    file_hash = hashlib.sha256(md_content).hexdigest()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature, file_hash)
               VALUES (?, ?, ?, ?, ?)""",
            ("GB-DUP", "已存在的规范", "国家标准", "强制性", file_hash),
        )

    # 尝试上传相同内容的文件
    import io
    md_file = io.BytesIO(md_content)
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("dup.md", md_file, "text/markdown")},
        data={"code": "GB-DUP-2", "title": "重复规范"},
    )
    assert resp.status_code == 200
    # 应该提示重复
    assert "已导入" in resp.text or "重复" in resp.text or "已存在" in resp.text
