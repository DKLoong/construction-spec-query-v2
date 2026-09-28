"""超长条文的子块切分（**仅用于向量侧**；FTS 侧无长度限制、已覆盖全文）。

切分口径（**R3**）：`plain_text(content)` 之后按 **字符**计数。
- 实测切分处数的是 `len(cleaned)`，与 embedding 模型的 512 **token** 上限只是同
  量级（中文 1 字≈1 token，本机 `models/BAAI/bge-small-zh-v1___5/tokenizer_config.json`
  的 `model_max_length: 512`），故常量名与文档一律声明为**字符**，不再自称 token，
  也不引入分词器依赖（`chunk_text` 保持纯函数）。
- **生效上限 = CHUNK_CHAR_LIMIT − 前缀长度**，前缀即 `build_embed_text` 会拼在正文
  之前的那一段；扣减**只有 `build_embed_chunks` 一处实现**——导入路径（import_routes）、
  重建路径（maintenance_routes / scripts/reindex_vectors.py）与补齐缺失向量
  （vector_search.index_missing）都调它。各算一套必然静默分叉：同一条超长条文
  经导入与经重建会写出不同的块。漏掉扣减时，末块的实际模型输入会超出 512 而被
  **静默截断**，恰好打掉本批「长条文尾部要可召回」的靶心。

重叠（**R4**）：上限 10% of limit，**且从整句的开头起**（对齐句末，不得从词中间切）；
计入块长度——放不下就放弃这一块的重叠，绝不超限。上一块末尾 10% 窗口内没有句末
标点（超长无标点串）时不重叠：宁可不重叠，也不从词中间切开。

不变量（**R6**，两条分别断言）：
① 覆盖性——原文每个字符至少属于一个块（首块从原文开头起、末块到原文末尾止）；
② 相邻块重叠 ≤ 上限。
**不再**是「拼接等于原文」：加了重叠后该性质在数学上必然不成立
（`["ABCD", "CDEF"]` 拼回 `"ABCDCDEF"`）。
"""
import re

from app.ai.text_clean import plain_text
from app.config import CHUNK_CHAR_FLOOR, CHUNK_CHAR_LIMIT
from app.search.embed_text import build_embed_text

# 句末标点（中英文）：既是切分点，也是「重叠从哪里开始」的对齐点（单一来源）
_SENTENCE_CHARS = "。；！？.!?;"
# **只用零宽 lookbehind**：split 后拼接严格等于原文，不吞任何字符。
# （早期写法带 `\s*` 会吃掉标点后的空白，覆盖性断言随之变味。）
_SENTENCE_END = re.compile(r"(?<=[" + _SENTENCE_CHARS + r"])")
# 重叠上限：10% of limit
_OVERLAP_RATIO = 0.10


def _split_units(text: str, limit: int) -> list[str]:
    """按句末标点切成句子；单句超过 limit 时按长度保底切。拼接严格等于 text。"""
    units: list[str] = []
    for piece in _SENTENCE_END.split(text):
        if not piece:
            continue
        while len(piece) > limit:
            units.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            units.append(piece)
    return units


def _sentence_tail(buf: str, overlap: int) -> str:
    """取 buf 末尾留给下一块重叠的部分：长度 ≤ overlap，且**从整句开头起**。

    「对齐句末」= 重叠片段必须起于句末标点之后，不得从词中间切；故在末尾 overlap
    个字符的窗口里找最后一个句末标点，取其后的整段。窗口内没有标点则返回空串
    （超长无标点串）：宁可不重叠，也不从词中间切开。

    ⚠ 查的是 `window[:-1]`：末尾那个标点正是 buf 自己的收尾标点，其后再无内容，
    取它只会得到空串（早期实现即如此，重叠恒为 0 且测不出来）。
    """
    if overlap <= 0 or not buf:
        return ""
    window = buf[max(0, len(buf) - overlap):]
    cut = max(window[:-1].rfind(ch) for ch in _SENTENCE_CHARS)
    return window[cut + 1:] if cut >= 0 else ""


def chunk_text(text: str, limit: int = CHUNK_CHAR_LIMIT) -> list[str]:
    """把正文切成不超过 limit 个**字符**的子块（重叠 ≤10%、对齐句末）。

    limit 是**生效上限**：生产调用方走 `build_embed_chunks`（它会先扣掉
    `build_embed_text` 的前缀长度），不要自己另算一套预留。

    空文本返回空列表（调用方据此跳过写向量）；不超过上限的文本返回单块。
    """
    cleaned = plain_text(text or "").strip()
    if not cleaned:
        return []
    if limit <= 0 or len(cleaned) <= limit:
        return [cleaned]

    overlap = int(limit * _OVERLAP_RATIO)
    chunks: list[str] = []
    buf = ""
    for unit in _split_units(cleaned, limit):
        if buf and len(buf) + len(unit) > limit:
            chunks.append(buf)
            tail = _sentence_tail(buf, overlap)
            # 重叠计入长度：放不下就丢掉重叠（绝不超限，超限即被模型静默截断）
            buf = tail if len(tail) + len(unit) <= limit else ""
        buf += unit
    if buf:
        chunks.append(buf)
    return chunks


def build_embed_chunks(content: str, *, code: str = "", spec_title: str = "",
                       clause_no: str = "", clause_title: str = "",
                       section_path: str = "",
                       limit: int = CHUNK_CHAR_LIMIT) -> list[str]:
    """把一条条文切成若干**embedding 输入文本**：每块一条 = 向量表一行。

    生效上限 = limit − 前缀长度 − 1（那 1 个字符是前缀与正文之间的分隔空格）。
    前缀长度**直接由 `build_embed_text` 自己算出来**（传空正文），不手写拼接，
    避免格式漂移时预留跟着算错。

    **全仓唯一做前缀预留的地方**：导入路径、重建路径、补齐缺失向量都必须调它。
    空正文返回 `[]`（调用方据此跳过写向量，不要为此写一条只有前缀的行）。
    """
    prefix_len = len(build_embed_text(code, spec_title, clause_no, clause_title, "",
                                      section_path))
    effective = max(limit - prefix_len - 1, CHUNK_CHAR_FLOOR)
    return [build_embed_text(code, spec_title, clause_no, clause_title, piece, section_path)
            for piece in chunk_text(content, limit=effective)]
