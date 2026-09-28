"""条文 embedding 文本构造（BGE 语义检索输入，与 FTS5 search_text 是两套）

embedding 文本喂给 BGE 模型生成语义向量；FTS5 的 search_text 由 tokenize 模块
jieba 预分词构建，两者用途不同、格式不同。统一抽成函数，避免 6 处调用点
（导入 phase2 / 维护重建 / 补齐缺失向量 / 条文编辑重索引 / 重建脚本 / 效果探针）
格式漂移导致向量不一致。

⚠ 新增调用点时**必须**传 `section_path`：它有默认值 `""`，漏传不会报错，
只会静默产出不含面包屑的向量（同一批条文两条路径向量不一致）。
`tests/test_embed_text.py::test_all_production_callers_pass_section_path`
与 `::test_production_caller_list_is_complete` 是这条约束的守卫。
"""

from app.ai.text_clean import plain_text


def build_embed_text(code: str = "", spec_title: str = "", clause_no: str = "",
                     clause_title: str = "", content: str = "",
                     section_path: str = "") -> str:
    """拼接规范 code/title + 条文号 + 面包屑 + 标题/正文，作为 embedding 输入。

    格式："{code} {spec_title} [{clause_no}] {section_path} {clause_title} {content}"
    **面包屑插在条文号之后、正文之前**：保留「该条属于哪一节」的位置信号，
    使「搜节名」能通过向量臂召回该节下的条文。

    空字段以空串占位，末尾 strip 去掉整体空白，避免空 title/content 残留多余空格。

    **content 先过 `plain_text` 去标记**：content 是渲染载荷（含 PaddleOCR-VL 的
    HTML 表格与 LaTeX），实测最长三张附录表的 content 有 92% 字符是标记——直接
    喂模型会让向量退化成「td style center」的语义。详见 `app/ai/text_clean.plain_text`。
    """
    return plain_text(f"{code or ''} {spec_title or ''} [{clause_no}] "
                      f"{section_path or ''} {clause_title or ''} {content or ''}").strip()
