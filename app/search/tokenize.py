"""jieba 预分词工具：FTS5 检索的 search_text / breadcrumb 两列构建与查询切词

索引侧（导入/编辑写 search_text + breadcrumb）与查询侧（sql_search 切 keyword）
都走本模块，固定精确模式（cut_all=False）+ 固定词典，保证分词一致性，无需人工介入。
"""

import re
import jieba

from app.ai.text_clean import plain_text


#: CJK 表意文字码位：基本区（U+4E00–9FFF）+ 扩展 A（U+3400–4DBF）+ 兼容表意文字（U+F900–FAFF）。
#: 中日韩**标点**（、。，）不在其中——它们不是「字」，两侧的空白不属词内空白。
_CJK_IDEOGRAPH = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

#: 词内空白 = 两侧都是 CJK 表意文字的一串空白。空白集合取「除换行外的任意空白」
#: （半角空格 U+0020、制表符、全角空格 U+3000 等）；排除换行是因为 section_path
#: 本就不含换行，真含也不该把两行黏成一行。
_INTRA_CJK_SPACE = re.compile(
    rf"(?<=[{_CJK_IDEOGRAPH}])[^\S\n\r]+(?=[{_CJK_IDEOGRAPH}])"
)


def normalize_cjk_spacing(text: str) -> str:
    """折叠**中日韩表意文字之间**的空白，其余字符逐字不动。

    用途（**仅**用于派生文本，见 `build_search_text`）：源文件把节名写成词内空格时
    （CJJ2 夹具实测：122 条 distinct 路径中 38 条含词内空格，如 `1 总 则` /
    `6 钢 筋` / `12 支 座` / `10 基 础 > 10.4 沉 井`），直接分词会把词的字面劈开
    ——jieba 对 `1 总 则` 给的是 `1 / 总 / 则`，于是查自然名「总则」**在任何权重下
    都 0 结果**。先折叠再分词即得整词 `总则`。

    只折叠「CJK 空白 CJK」，故下列形态**逐字不变**（回归用例逐 token 钉住）：
    - ` > ` 路径分隔符（`>` 非 CJK，两侧空白保留）
    - `GB 50010` 等纯 ASCII 段
    - `6.3.1 接头`（数字后接 CJK）与 `6 混凝土分项工程`（编号与节名之间的空白）
    - 中日韩标点旁的空白（`、` 不是表意文字）

    幂等：`normalize_cjk_spacing(normalize_cjk_spacing(x)) == normalize_cjk_spacing(x)`。
    """
    if not text:
        return text
    return _INTRA_CJK_SPACE.sub("", text)


def tokenize(text: str) -> list[str]:
    """jieba 精确模式切词，过滤空白与纯标点 token。

    - 中文按词组分词（如「钢筋混凝土」→ 钢筋/混凝土）
    - 纯标点（如 jieba 把 "5.1.1" 拆出的 "."）过滤，避免污染 FTS 查询
    - 确定性：同输入同词典同模式 → 同输出
    """
    words = []
    for w in jieba.cut(text or "", cut_all=False):
        w = w.strip()
        if w and not re.fullmatch(r"\W+", w):
            words.append(w)
    return words


def build_search_text(clause_no: str, title: str, content: str,
                      section_path: str = "") -> tuple[str, str]:
    """构建 FTS5 索引的**两列**文本：`(search_text, breadcrumb)`。

    - `search_text`：`jieba(标题+正文)` 空格连接 + 追加 `clause_no` 原文。
      **不含面包屑**——面包屑只通过 `breadcrumb` 列参与，否则会以权重 1.0
      （埋在正文里）与 `breadcrumb_weight` 各计一次，权重语义失真。
    - `breadcrumb`：`jieba(normalize_cjk_spacing(section_path))`。**必须预分词**：FTS5
      默认 unicode61 分词器把连续中文折叠为单个 token，未分词的整段中文列只能
      整段精确命中（实测查「接头安装」命中、查「接头」「安装」0 命中）。
      分词前先折叠**词内空白**（见 `normalize_cjk_spacing`）：源文件把节名写成
      `1 总 则` 时，直接分词只得 `1 / 总 / 则`，查「总则」永远 0 结果。
      ⚠ 这是**派生文本就地清洗**（与 `plain_text` 同一条边界）：`section_path`
      存储列与界面展示**逐字保留**源文件原文，不得在此顺手改它。
      ⚠ 改了本列的口径 → 既有库必须**重建 FTS 索引**才生效（存量行不会自愈）。
    - `content` **先过 `plain_text` 去标记**（渲染载荷含 PaddleOCR-VL 的
      HTML/LaTeX，直接分词会把标记灌进索引，实测约占索引 20-25%）。
    - `search_text` 空时返回占位空格，避免 FTS5 索引 NULL 报 datatype mismatch。
    """
    parts = tokenize(plain_text(f"{title or ''} {content or ''}"))
    if clause_no and clause_no.strip():
        parts.append(clause_no.strip())
    search_text = " ".join(parts) or " "
    breadcrumb = " ".join(tokenize(normalize_cjk_spacing(section_path or "")))
    return search_text, breadcrumb


def build_match_query(keyword: str, join_with: str = "AND") -> str:
    """查询侧切词 → FTS5 MATCH 查询串。

    - 每个 token 加双引号包裹（短语查询）并转义内部引号，防止 FTS5 语法注入
    - join_with 默认 AND（隐式「同时出现」语义）；多词 AND 无结果时，
      调用方用 join_with="OR" 降级召回（任一 token 命中即返回）
    - keyword 切不出有效词时返回空串（调用方走纯 SQL 分支）
    """
    toks = tokenize(keyword)
    if not toks:
        return ""
    return f" {join_with} ".join('"' + t.replace('"', '""') + '"' for t in toks)
