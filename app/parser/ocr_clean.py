"""OCR 输出文本的保守清洗

处理 OCR 输出的 Markdown 文本（含 `## 第X页` 分隔），仅删除明确无信息量的行：
1. 孤立页码行：独立成行的 `第 X 页` / `第X页`（含全角空格）
2. 纯页码数字行：独立成行的 1-3 位纯数字
3. `[OCR 失败: xxx]` 标记行
4. 完全重复的短页眉行：整行重复出现 ≥3 次、≤20 字符、且非句子/标点密集

注意：
- 禁止句子级断行合并、表格复原、依赖外部库，保持保守策略。
- `## 第X页` 是 OCR 管线的结构分隔标记，**不删除**——保留它才能让
  parse_markdown 为封面页生成独立条文，再由 import 侧的 is_cover_clause 丢弃。
"""
import re
from collections import Counter


# 孤立页码行：`第 1 页`、`第1页`、`第　3　页`（全角空格）
# 注意：不含 `## 第X页` 结构分隔标记（该标记须保留供封面过滤使用）
_PAGE_LINE_RE = re.compile(r"^第[\s　]*\d+[\s　]*页\s*$")
# 纯页码数字行（1-3 位，与页码语义吻合）
_PURE_NUMBER_RE = re.compile(r"^\d{1,3}$")
# OCR 失败标记行：`[OCR 失败: ...]` 或 Paddle 包裹的 `_[OCR 失败: ...]_`
_OCR_FAIL_RE = re.compile(r"^_?\[OCR 失败")
# 页眉行长度上限与最少重复次数
_REPEAT_HEADER_MAX_LEN = 20
_REPEAT_HEADER_MIN_COUNT = 3
# 标点密度阈值：超过视为句子/装饰线，不删除（如 "--------"、"........."）
_PUNCT_DENSITY_THRESHOLD = 0.4
# 句末标点：命中则视为完整句子，不作为重复页眉删除
_SENTENCE_END = ("。", "；", "：", "！", "？")


def _punct_ratio(s: str) -> float:
    """计算行内非字母数字字符占比（近似标点密度）

    Python 的 str.isalnum() 对中文汉字返回 True，因此该值仅统计
    标点、空格、符号等「非汉字/字母/数字」字符。
    """
    if not s:
        return 0.0
    non_alnum = sum(1 for ch in s if not ch.isalnum())
    return non_alnum / len(s)


def clean_ocr_text(md_text) -> str:
    """保守清洗 OCR 输出的 Markdown 文本，返回清洗后的文本

    - 只删除明确无信息量的整行，绝不做断行合并/表格复原
    - 幂等：对已清洗文本再次调用结果不变
    """
    if not md_text:
        return ""

    lines = md_text.split("\n")
    stripped = [ln.strip() for ln in lines]
    # 预先统计整行出现次数，供规则 4 使用（基于原始文本全量计数）
    counter = Counter(stripped)

    cleaned = []
    for ln, s in zip(lines, stripped):
        # 空行直接保留（维持原文段落结构）
        if not s:
            cleaned.append(ln)
            continue

        # 规则1：孤立页码行
        if _PAGE_LINE_RE.fullmatch(s):
            continue
        # 规则2：纯页码数字行
        if _PURE_NUMBER_RE.fullmatch(s):
            continue
        # 规则3：OCR 失败标记行
        if _OCR_FAIL_RE.match(s):
            continue
        # 规则4：完全重复的短页眉行（整行重复 ≥3 次）
        if len(s) <= _REPEAT_HEADER_MAX_LEN and counter[s] >= _REPEAT_HEADER_MIN_COUNT:
            # 排除完整句子与标点密集行（装饰线等），保守删除
            if not s.endswith(_SENTENCE_END) and _punct_ratio(s) < _PUNCT_DENSITY_THRESHOLD:
                continue

        cleaned.append(ln)
    return "\n".join(cleaned)
