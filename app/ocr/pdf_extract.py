from typing import cast

import fitz  # PyMuPDF


def extract_text(pdf_path: str) -> str:
    """提取 PDF 文本层文本"""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        # 无参 get_text() 运行时必为 str；PyMuPDF 的 stub 把各重载（"text"/"dict"/
        # "list"…）合并成 `str | list | dict`，无法据此窄化，故显式 cast 固化意图。
        text = cast(str, page.get_text())
        if text.strip():
            pages.append(text.strip())
    doc.close()
    return "\n\n".join(pages)


def is_scanned(pdf_path: str) -> bool:
    """判断 PDF 是否为扫描件（文本层为空）"""
    try:
        text = extract_text(pdf_path)
        return len(text.strip()) < 100
    except Exception:
        return False
