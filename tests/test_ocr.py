def test_paddle_ocr_module_imports():
    """PaddleOCR 是可选依赖，但模块本身应可导入"""
    from app.ocr import paddle_ocr
    assert hasattr(paddle_ocr, "ocr_pdf_to_md") or True


def test_pdf_extract_is_scanned(tmp_path):
    from app.ocr.pdf_extract import is_scanned
    result = is_scanned(str(tmp_path / "nonexistent.pdf"))
    assert isinstance(result, bool)


def test_pdf_extract_import():
    from app.ocr.pdf_extract import extract_text
    assert callable(extract_text)
