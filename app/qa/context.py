"""QA 上下文组装纯函数层（无 IO，可独立单测）

核心原则：**回答准确性优先，宁可舍弃部分候选条目，绝不截断条文内部内容。**
本模块只做纯计算（Token 估算 / 摘要 / 元信息 / 元数据过滤 / 分数阈值过滤 /
动态条数选取 / 强弱分层 / 预算兜底组装），由 `qa_routes.py` 编排调用。
"""
import math
from dataclasses import dataclass, field

# 默认常量（实际运行值由 app/qa/config.py 从 DB settings 读取，这里仅兜底）
_DEFAULT_CHARS_PER_TOKEN = 2
_DEFAULT_SUMMARY_LIMIT = 200


def strip_html_content(content: str) -> str:
    """剥离 OCR/Markdown 残留的 HTML 标记（复用 AI 清洗函数）"""
    from app.ai.text_clean import strip_html
    return strip_html(content or "")


def estimate_tokens(text: str, chars_per_token: int = _DEFAULT_CHARS_PER_TOKEN) -> int:
    """估算文本 Token 数（字符数近似）。

    - 规范条文以中文为主，中文平均约 1 字 ≈ 0.5~1 token，取 len/2 偏保守。
    - chars_per_token 可配置（qa.token.chars_per_token）。
    - 纯函数，后续可替换为 tiktoken/后端 tokenizer 实现，签名不变。
    """
    if not text:
        return 0
    return math.ceil(len(text) / max(chars_per_token, 1))


def _close_math(text: str) -> str:
    """保证 LaTeX 公式闭合（$ 成对），避免奇数 $ 让下游解析错乱。

    截断/摘要可能落在公式中间，奇数个 $ 会把后续文本误当公式；
    回退到最后一个 $ 之前截断（与 search_routes._safe_summary 同逻辑）。
    """
    if text.count("$") % 2 == 1:
        idx = text.rfind("$")
        if idx > 0:
            text = text[:idx]
    return text


def make_summary(content: str, summary_limit: int = _DEFAULT_SUMMARY_LIMIT,
                 title: str = "") -> str:
    """启发式条文摘要（不做模型摘要，零成本可测）：

    1. strip_html 去标记；
    2. 取首个非空段（若 <20 字符取第二段）；
    3. 超限在句子边界截断（回退到最后一个 。；！？：）；
    4. LaTeX $ 闭合；
    5. 条文有 title 时前缀 `[title] `。
    """
    clean = strip_html_content(content).strip()
    if not clean:
        return ""
    paras = [p.strip() for p in clean.split("\n") if p.strip()]
    if not paras:
        return ""
    seg = paras[0]
    if len(seg) < 20 and len(paras) > 1:
        seg = paras[1]

    if len(seg) > summary_limit:
        cut = seg[:summary_limit]
        last_punct = max(cut.rfind(c) for c in "。；！？：")
        if last_punct > summary_limit // 2:
            seg = cut[:last_punct + 1]
        else:
            seg = cut

    seg = _close_math(seg)
    if title:
        seg = f"[{title}] {seg}"
    return seg.strip()


def build_meta(spec_code: str = "", spec_title: str = "",
               spec_status: str = "", clause_no: str = "",
               dim3_usage: str = "") -> str:
    """结构化元信息一行（高/次相关条目都强制带上）。"""
    scope = (dim3_usage or "").strip() or "适用范围未标注"
    return (
        f"【《{spec_code or '未知规范'}》{spec_title or ''}"
        f"｜{spec_status or '状态未标注'}｜条文 {clause_no or '?'}"
        f"｜适用范围：{scope}】"
    )


@dataclass
class RerankedItem:
    """精排后的单条候选（含分层所需的全部预计算字段）。"""
    clause: dict                     # 原始候选 dict（含 spec_code/spec_title/spec_status）
    score: float
    tier: str                        # "high" | "low"
    content_clean: str               # strip_html 后全文（high 用）
    summary: str                     # 启发式摘要（low 用，verbatim 模式备选）
    meta: str                        # 结构化元信息一行
    tokens_full: int                 # meta + 全文 的估算 token
    tokens_summary: int              # meta + 摘要 的估算 token
    _extra: dict = field(default_factory=dict, repr=False)


def filter_by_metadata(
    candidates: list[dict],
    include_invalid: bool,
    status_allow: tuple[str, ...] = ("现行",),
    industry_allow: tuple[str, ...] = (),
    dept_allow: tuple[str, ...] = (),
) -> list[dict]:
    """RRF 后、CrossEncoder 前执行：元数据过滤（默认过滤废止/已替代规范）。

    - include_invalid=True：跳过全部过滤（用户明确指定查询废止规范）。
    - 候选缺 spec_status（未打标）时默认放行，避免误杀。
    - industry_allow / dept_allow 当前无数据字段，空元组=不过滤（预留钩子）。
    """
    if include_invalid:
        return candidates
    out = []
    for c in candidates:
        st = c.get("spec_status") or ""
        if st and st not in status_allow:
            continue
        if industry_allow and (c.get("dim3_usage") or "") not in industry_allow:
            continue
        if dept_allow and (c.get("dim1_hierarchy") or "") not in dept_allow:
            continue
        out.append(c)
    return out


def filter_by_score(ranked: list[tuple[dict, float]], min_score: float) -> list[tuple[dict, float]]:
    """丢弃低于 min_score 的候选（含边界值）。返回按分数降序的 (候选, 分数) 列表。

    后续动态条数统计的基数为过滤后的有效候选集，不是原始召回集合。
    """
    return [(c, s) for c, s in ranked if s >= min_score]


def dynamic_select(
    effective_count: int,
    top_ratio: float = 0.10,
    min_results: int = 10,
    max_results: int = 15,
) -> int:
    """计算送入上下文的条数上限（在阈值过滤后的有效候选集上执行）。

    - 取排序靠前 top_ratio 向上取整；
    - 不足 min_results 保底；
    - 有效候选集 < min_results 全取；
    - 结果不得超过 max_results 硬上限（防海量结果上下文爆炸）。
    """
    if effective_count <= 0:
        return 0
    if effective_count < min_results:
        return effective_count
    k = math.ceil(top_ratio * effective_count)
    k = max(k, min_results)
    return min(k, max_results)


def tier_items(
    ranked: list[tuple[dict, float]],
    high_threshold: float,
    min_score: float,
    summary_limit: int = _DEFAULT_SUMMARY_LIMIT,
) -> tuple[list[RerankedItem], list[RerankedItem]]:
    """把过滤后候选按分数分为 (高相关, 次相关)，默认已按分数降序。

    - score >= high_threshold → 高相关（完整原文）
    - min_score <= score < high_threshold → 次相关（仅摘要）
    - score < min_score → 丢弃（双保险）
    """
    high: list[RerankedItem] = []
    low: list[RerankedItem] = []
    for c, s in ranked:
        if s < min_score:
            continue
        clean = strip_html_content(c.get("content") or "")
        title = c.get("title") or ""
        meta = build_meta(
            c.get("spec_code") or "", c.get("spec_title") or "",
            c.get("spec_status") or "", c.get("clause_no") or "",
            c.get("dim3_usage") or "",
        )
        summary = make_summary(clean, summary_limit, title)
        item = RerankedItem(
            clause=c, score=s, tier="high" if s >= high_threshold else "low",
            content_clean=clean, summary=summary, meta=meta,
            tokens_full=estimate_tokens(meta + clean),
            tokens_summary=estimate_tokens(meta + summary),
        )
        if s >= high_threshold:
            high.append(item)
        else:
            low.append(item)
    return high, low


def build_context(
    high: list[RerankedItem],
    low: list[RerankedItem],
    verbatim: bool = False,
    budget: int = 6000,
) -> tuple[str, int, int, list[tuple["RerankedItem", str]]]:
    """组装上下文。返回 (context_str, context_tokens, dropped_count, picked)。

    picked 为实际装入上下文的 (RerankedItem, text) 列表（供来源引用提取）。

    - 高相关在前、次相关在后，各区间内已按分数降序排列。
    - verbatim=True：次相关条目也提供全文（摘抄模式需要原文可抄）。
    - 单条 token > budget/2 视为超长条文，直接丢弃（避免一条挤占全部预算）。
    - 贪心从高分到低分装入；超预算丢弃整条（等价按分数低→高丢弃整条）。
    - **绝不截断单条条文内部文本**。
    """
    items: list[tuple[RerankedItem, str, int]] = []
    for it in high + low:
        if it.tier == "high" or verbatim:
            text, tok = f"{it.meta}\n{it.content_clean}", it.tokens_full
        else:
            text, tok = f"{it.meta}\n{it.summary}", it.tokens_summary
        items.append((it, text, tok))

    used = 0
    dropped = 0
    picked: list[tuple[RerankedItem, str]] = []
    for it, text, tok in items:
        if tok > budget // 2:
            dropped += 1
            continue
        if used + tok > budget:
            dropped += 1
            continue
        picked.append((it, text))
        used += tok

    sections = []
    high_picked = [t for it, t in picked if it.tier == "high"]
    low_picked = [t for it, t in picked if it.tier == "low"]
    if high_picked:
        sections.append("── 高相关条文（可作为直接依据）──\n" + "\n\n".join(high_picked))
    if low_picked:
        sections.append("── 次相关条文（仅供参考，不可作为主要依据）──\n" + "\n\n".join(low_picked))
    return "\n\n".join(sections), used, dropped, picked
