"""jieba 预分词工具：FTS5 检索的 search_text / breadcrumb 两列构建与查询切词

索引侧（导入/编辑写 search_text + breadcrumb）与查询侧（sql_search 切 keyword）
都走本模块，固定精确模式（cut_all=False）+ 固定词典，保证分词一致性，无需人工介入。
"""

import re
import jieba

from app.ai.text_clean import plain_text


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
    - `breadcrumb`：`jieba(section_path)`。**必须预分词**：FTS5 默认
      unicode61 分词器把连续中文折叠为单个 token，未分词的整段中文列只能
      整段精确命中（实测查「接头安装」命中、查「接头」「安装」0 命中）。
    - `content` **先过 `plain_text` 去标记**（渲染载荷含 PaddleOCR-VL 的
      HTML/LaTeX，直接分词会把标记灌进索引，实测约占索引 20-25%）。
    - `search_text` 空时返回占位空格，避免 FTS5 索引 NULL 报 datatype mismatch。
    """
    parts = tokenize(plain_text(f"{title or ''} {content or ''}"))
    if clause_no and clause_no.strip():
        parts.append(clause_no.strip())
    search_text = " ".join(parts) or " "
    breadcrumb = " ".join(tokenize(section_path or ""))
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
