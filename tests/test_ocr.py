def test_get_setting_returns_value(monkeypatch, tmp_path):
    """get_setting 从数据库读取值"""
    db_path = tmp_path / "test_settings.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('ocr.access_token', 'abc123')")

    from app.ocr.paddle_api import get_setting
    assert get_setting("ocr.access_token") == "abc123"


def test_get_setting_missing_key_returns_empty(monkeypatch, tmp_path):
    """不存在的 key 返回空字符串"""
    db_path = tmp_path / "test_settings_empty.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    from app.ocr.paddle_api import get_setting
    assert get_setting("nonexistent") == ""


def test_paddle_studio_api_ocr_image(monkeypatch, tmp_path):
    """ocr_image 调用 AI Studio API 并返回文本"""
    db_path = tmp_path / "test_api.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('ocr.access_token', 'test-token')")

    from app.ocr.paddle_api import PaddleStudioAPI
    api = PaddleStudioAPI(access_token="test-token")

    # 用 monkeypatch 模拟 httpx 响应
    import httpx

    class MockResponse:
        status_code = 200
        def json(self):
            return {"words_result": [{"words": "第一条"}, {"words": "第二条"}],
                    "words_result_num": 2}
        def raise_for_status(self):
            pass

    async def mock_post(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    # 创建一个临时文件供 OCR 读取
    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"fake_image_data")

    import asyncio
    text = asyncio.run(api._ocr_image_async(str(test_img)))
    assert "第一条" in text
    assert "第二条" in text


def test_pdf_extract_import():
    """pdf_extract 模块不受影响"""
    from app.ocr.pdf_extract import extract_text, is_scanned
    assert callable(extract_text)
    assert callable(is_scanned)
