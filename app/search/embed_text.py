"""条文 embedding 文本构造（BGE 语义检索输入，与 FTS5 search_text 是两套）

embedding 文本喂给 BGE 模型生成语义向量；FTS5 的 search_text 由 tokenize 模块
jieba 预分词构建，两者用途不同、格式不同。统一抽成函数，避免 4 处调用点
（导入 phase2 / 维护重建 / 补齐缺失向量 / 条文编辑重索引）格式漂移导致向量不一致。
"""

from app.ai.text_clean import plain_text


def build_embed_text(code: str = "", spec_title: str = "", clause_no: str = "",
                     clause_title: str = "", content: str = "") -> str:
    """拼接规范 code/title + 条文号/标题/正文，作为语义检索的 embedding 输入。

    格式："{code} {spec_title} [{clause_no}] {clause_title} {content}"
    空字段以空串占位，末尾 strip 去掉整体空白，避免空 title/content 残留多余空格。

    **content 先过 `plain_text` 去标记**：content 是渲染载荷（含 PaddleOCR-VL 的
    HTML 表格与 LaTeX），实测最长三张附录表的 content 有 92% 字符是标记——直接
    喂模型会让向量退化成「td style center」的语义。详见 `app/ai/text_clean.plain_text`。
    """
    return plain_text(f"{code or ''} {spec_title or ''} [{clause_no}] "
                      f"{clause_title or ''} {content or ''}").strip()
