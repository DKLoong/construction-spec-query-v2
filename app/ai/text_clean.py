"""文本清理工具：剥离条文内容中的 HTML/OCR 残留标记，仅保留可读文本

OCR/PDF 导入的条文 content 中常混有 <div style=...>、<br>、<table> 等标记
（markdown 转换残留）。这些标记不应进入 AI 分类/问答上下文，本模块统一清理。

`plain_text` 与 `strip_html` 的分工（2026-09-19 引入，详见 TODOS 与 spec）：
- `clauses.content` 是**渲染载荷**——详情页走 marked + KaTeX 渲染表格/公式，
  因此正文入库时**不得清洗**，标记要原样保留。
- 但索引/向量/提词/精排这些**派生文本**必须先去标记，否则 `td`/`style`/`word`
  这类标记会被切成 token 灌进 FTS5 与 embedding（实测约占索引 20-25%）。
- 故：**派生文本用 `plain_text`，渲染载荷保持原文**。
"""
import re
from html.parser import HTMLParser

# 块级标签：在文本流中当作换行处理，避免相邻内容粘连
_BLOCK_TAGS = {
    "div", "p", "br", "table", "tr", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
}

# 表格单元格标签：strip_html 只把 tr 当块级，相邻 <td> 文字会粘连
# （实测「接头类型」+「连接件型式」→「接头类型连接件型式」），故先转为空格
_CELL_TAG = re.compile(r"</?t[dh][^>]*>", re.I)

# LaTeX 数学段：PaddleOCR-VL 对公式输出 $...$（含 $$...$$）或 \(...\) / \[...\]
# 实测 17 条含公式的条文 $ 全部成对、定界符外无裸命令，故整段丢弃即可
_LATEX = re.compile(r"\$\$.*?\$\$|\$[^$\n]{1,200}\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)


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


def plain_text(text: str | None) -> str:
    """派生文本专用清洗：去 HTML 标记 + 丢 LaTeX 段 + 单元格分隔

    仅用于**派生文本**（FTS 索引 / 向量 embedding / 提词 / 精排输入）；
    `clauses.content` 是渲染载荷，**不要**用本函数覆盖它。

    - 单元格标签（<td>/<th>）先转空格，避免相邻单元格文字粘连
    - 复用 `strip_html` 去标签并解码实体
    - LaTeX 数学段整段丢弃（符号名 `f_{yk}`、单位 `N/mm^{2}` 判定为不可检索）
    - 末尾再走一次 `strip_html` 归并空白，保证**幂等**：
      非标记条文的派生结果与原文一致，不会造成全库索引口径漂移
    """
    if not text:
        return ""

    spaced = _CELL_TAG.sub(" ", text)
    return strip_html(_LATEX.sub(" ", strip_html(spaced)))
