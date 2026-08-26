"""jieba 预分词工具：FTS5 检索的 search_text 构建与查询切词

索引侧（导入/编辑写 search_text）与查询侧（sql_search 切 keyword）都走本模块，
固定精确模式（cut_all=False）+ 固定词典，保证分词一致性，无需人工介入。
"""

import re
import jieba


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


def build_search_text(clause_no: str, title: str, content: str) -> str:
    """构建 FTS5 索引文本：jieba(标题+正文) 空格连接 + 追加 clause_no 原文。

    - clause_no 不走 jieba（点号会被拆成独立 token），直接追加原文，
      由 FTS5 的 unicode61 分词器统一切分（"5.1.1" → 5/1/1 三个 token）
    - 空文本返回占位空格，避免 FTS5 索引 NULL 值报 datatype mismatch
    """
    parts = tokenize(f"{title or ''} {content or ''}")
    if clause_no and clause_no.strip():
        parts.append(clause_no.strip())
    return " ".join(parts) or " "


def build_match_query(keyword: str) -> str:
    """查询侧切词 → FTS5 MATCH 查询串。

    - 每个 token 加双引号包裹（短语查询）并转义内部引号，防止 FTS5 语法注入
    - token 间用 AND 连接（隐式「同时出现」语义）
    - keyword 切不出有效词时返回空串（调用方走纯 SQL 分支）
    """
    toks = tokenize(keyword)
    if not toks:
        return ""
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in toks)
