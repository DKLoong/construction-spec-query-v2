"""文本清理工具：剥离条文内容中的 HTML/OCR 残留标记，仅保留可读文本

OCR/PDF 导入的条文 content 中常混有 <div style=...>、<br>、<table> 等标记
（markdown 转换残留）。这些标记不应进入 AI 分类/问答上下文，本模块统一清理。
"""
import re
from html.parser import HTMLParser

# 块级标签：在文本流中当作换行处理，避免相邻内容粘连
_BLOCK_TAGS = {
    "div", "p", "br", "table", "tr", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class _MarkupToText(HTMLParser):
    """把 HTML 片段转换为纯文本：剥离标签、解码实体、块级标签转换行"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._last_was_newline = False

    def handle_data(self, data):
        if data:
            self.parts.append(data)
            self._last_was_newline = False

    def handle_starttag(self, tag, attrs):
        del attrs  # 标准库回调签名，attrs 当前不需要
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag, attrs):
        del attrs  # 标准库回调签名，attrs 当前不需要
        if tag in _BLOCK_TAGS:
            self._newline()

    def _newline(self):
        if self.parts and not self._last_was_newline:
            self.parts.append("\n")
            self._last_was_newline = True


def strip_html(text: str | None) -> str:
    """清理文本中的 HTML 标记残留，返回可读纯文本

    - 剥离 <div>/<table>/<td> 等标签，保留标签内文本
    - <br> 及块级标签转换为换行
    - HTML 实体（如 &nbsp;）解码为对应字符
    - 压缩多余空白、去除空行
    """
    if not text:
        return ""

    parser = _MarkupToText()
    try:
        parser.feed(text)
        parser.close()
        raw = "".join(parser.parts)
    except Exception:
        # HTML 解析异常时退化为正则剥离
        raw = re.sub(r"<[^>]+>", " ", text)

    lines = []
    for ln in raw.split("\n"):
        cleaned = re.sub(r"[ \t　]+", " ", ln).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)
