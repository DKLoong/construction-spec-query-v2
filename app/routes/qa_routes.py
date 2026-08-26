"""AI 问答路由（QA 优化版）

完整链路：
  用户提问 → RRF 混合召回 → 元数据过滤 → CrossEncoder 重排打分
  → 分数阈值过滤 → 动态条数选取 → 强弱相关分层
  → 上下文组装 & Token 溢出兜底 → 大模型 Prompt 推理 → 埋点

核心原则：准确性优先，宁可舍弃部分候选条目，绝不截断条文内部内容。
"""
import json
import logging
import time
from dataclasses import dataclass, field, asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.models import QaRequest, QAResponse, SearchQuery
from app.ai.api_client import APIBackend
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# 记录本次请求实际生效的精排器（crossencoder / vector / none），供阈值选型与埋点
_last_rerank_used = "none"


# ── 精排降级链（CrossEncoder → bi-encoder 向量 → 原始顺序） ──

def _rerank_scored(question: str, candidates: list[dict]) -> list[tuple[dict, float]]:
    """精排打分，返回 (候选, 分数) 按分数降序全部候选（不在此截断条数）。

    - 候选 ≤ 1：直接返回 [(c, 1.0)]，不打分、不分层。
    - CrossEncoder 可用：分数 = 模型输出（量纲约 0~1）。
    - 降级 bi-encoder 向量：分数 = 余弦相似度（量纲 -1~1），阈值用 qa.vector.* 独立集。
    """
    global _last_rerank_used
    if len(candidates) <= 1:
        _last_rerank_used = "none"
        return [(c, 1.0) for c in candidates]

    texts = [(c.get("content") or "")[:300] for c in candidates]

    try:
        from app.ai.reranker import rerank
        scores = rerank(question, texts)
        if scores is not None:
            _last_rerank_used = "crossencoder"
            ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
            return [(c, float(s)) for c, s in ranked]
    except Exception as e:
        logger.warning("CrossEncoder 精排异常: %s", e)

    try:
        from app.ai.embedding import embed_texts
        import numpy as np
        q_vec = np.array(embed_texts([question])[0], dtype=np.float32)
        emb = np.array(embed_texts(texts), dtype=np.float32)
        scores = np.dot(emb, q_vec)
        _last_rerank_used = "vector"
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [(c, float(s)) for c, s in ranked]
    except Exception as e:
        logger.warning("向量重排序失败，降级为原始顺序: %s", e)

    _last_rerank_used = "none"
    return [(c, 1.0) for c in candidates]


def _extract_sources(clauses: list[dict]) -> list[dict]:
    """从实际进入上下文的条文提取去重引文来源（含 clause_id 供前端弹详情）"""
    seen = set()
    sources = []
    for r in clauses:
        key = (r.get("spec_code"), r.get("clause_no"))
        if key not in seen and key[0] and key[1]:
            seen.add(key)
            sources.append({
                "code": key[0], "clause_no": key[1],
                "clause_id": r.get("id"),
            })
    return sources


# ── 埋点观测 ──

@dataclass
class QATrace:
    """QA 请求级观测数据（各阶段数量 + 上下文指标），用于参数调优。"""
    question: str = ""
    mode: str = "rag"
    backend: str = ""
    include_invalid: bool = False
    rrf_total: int = 0
    pool_size: int = 0
    after_meta: int = 0
    after_threshold: int = 0
    select_target: int = 0
    high_count: int = 0
    low_count: int = 0
    context_tokens: int = 0
    budget: int = 0
    dropped_overflow: int = 0
    context_empty: bool = False
    rerank_used: str = ""
    duration_ms: int = 0
    _extra: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_extra", None)
        return d


def _emit_trace(trace: QATrace) -> None:
    """输出结构化 JSON 日志 + 落库 qa_request_logs（供分位数分析调参）。"""
    logger.info("[QA_TRACE] %s", json.dumps(trace.to_dict(), ensure_ascii=False))
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO qa_request_logs (
                    question, mode, backend, include_invalid, rrf_total, pool_size,
                    after_meta, after_threshold, select_target, high_count, low_count,
                    context_tokens, budget, dropped_overflow, context_empty, rerank_used, duration_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trace.question, trace.mode, trace.backend,
                    1 if trace.include_invalid else 0,
                    trace.rrf_total, trace.pool_size, trace.after_meta,
                    trace.after_threshold, trace.select_target,
                    trace.high_count, trace.low_count, trace.context_tokens,
                    trace.budget, trace.dropped_overflow,
                    1 if trace.context_empty else 0,
                    trace.rerank_used, trace.duration_ms,
                ),
            )
    except Exception as e:
        logger.warning("QA trace 落库失败: %s", e)


@router.post("/qa/ask")
async def qa_ask(request: Request, body: QaRequest):
    """AI 问答：检索 → 元数据过滤 → 精排 → 阈值过滤 → 动态条数 → 分层 → Token 兜底 → 推理"""
    from app.qa.context import (
        filter_by_metadata, filter_by_score, dynamic_select,
        tier_items, build_context,
    )
    from app.qa.config import get_qa_float, get_qa_int, get_qa_str
    from app.ai.prompts import build_system_prompt
    from app.search.hybrid_search import hybrid_search
    from app.ai.cli_client import get_backend
    from app.config import WORKSPACE_DIR

    question = body.question.strip()
    if not question:
        return JSONResponse({"detail": "问题不能为空"}, status_code=400)

    trace = QATrace(
        question=question[:100], mode=body.mode,
        backend=body.backend or "", include_invalid=body.include_invalid,
    )

    # ① RRF 混合召回（候选池大小走配置）
    pool = get_qa_int("retrieve.candidate_pool")
    keyword_mentions_non_clause = ("前言" in question) or ("条文说明" in question)
    has_dim = any([
        body.dim1_hierarchy, body.dim1_nature, body.dim2_stage, body.dim3_usage,
        body.dim4_specialty, body.dim5_location, body.dim6_material,
    ])
    sq = SearchQuery(
        keyword=question, per_page=pool,
        dim1_hierarchy=body.dim1_hierarchy, dim1_nature=body.dim1_nature,
        dim2_stage=body.dim2_stage, dim3_usage=body.dim3_usage,
        dim4_specialty=body.dim4_specialty, dim5_location=body.dim5_location,
        dim6_material=body.dim6_material,
        include_non_clause=keyword_mentions_non_clause,
    )
    try:
        candidates, total = hybrid_search(sq)
    except Exception as e:
        logger.error("QA hybrid_search failed: %s", e)
        candidates, total = [], 0
    trace.rrf_total = total
    trace.pool_size = len(candidates)

    # 兜底：分类筛选使候选过少时放宽为全局检索
    if has_dim and len(candidates) < get_qa_int("retrieve.qa_min_candidates"):
        logger.info("QA 分类筛选候选过少，放宽为全局检索")
        wide_sq = SearchQuery(
            keyword=question, per_page=pool,
            include_non_clause=keyword_mentions_non_clause,
        )
        try:
            candidates, _ = hybrid_search(wide_sq)
        except Exception as e:
            logger.error("QA hybrid_search (wide) failed: %s", e)

    # ② 元数据过滤（RRF 后、CrossEncoder 前；默认过滤废止/已替代规范）
    status_allow = tuple(
        s.strip() for s in get_qa_str("meta.status_allow").split(",") if s.strip()
    )
    candidates = filter_by_metadata(
        candidates, body.include_invalid, status_allow=status_allow,
    )
    trace.after_meta = len(candidates)

    # ③④ 精排打分 + 分数阈值过滤（分 CrossEncoder / 降级向量两套阈值集）
    ranked = _rerank_scored(question, candidates)
    trace.rerank_used = _last_rerank_used
    if trace.rerank_used == "crossencoder":
        min_score = get_qa_float("rerank.min_score")
        high_thr = get_qa_float("rerank.high_threshold")
    else:
        min_score = get_qa_float("vector.min_score")
        high_thr = get_qa_float("vector.high_threshold")
    ranked = filter_by_score(ranked, min_score)
    trace.after_threshold = len(ranked)

    # ⑤ 动态条数选取（基数为阈值过滤后的有效候选集）
    k = dynamic_select(
        len(ranked),
        top_ratio=get_qa_float("retrieve.top_ratio"),
        min_results=get_qa_int("retrieve.min_results"),
        max_results=get_qa_int("retrieve.max_results"),
    )
    trace.select_target = k
    ranked = ranked[:k]

    # ⑥ 强弱相关分层
    summary_limit = get_qa_int("token.summary_chars")
    high, low = tier_items(ranked, high_thr, min_score, summary_limit)
    trace.high_count, trace.low_count = len(high), len(low)

    # ⑦ Token 兜底组装（绝不截断单条内部文本，超预算丢弃整条）
    budget = get_qa_int("token.max_context_tokens")
    context_str, used_tok, dropped, picked = build_context(
        high, low, body.mode == "verbatim", budget,
    )
    trace.context_tokens, trace.budget = used_tok, budget
    trace.dropped_overflow = dropped
    trace.context_empty = not context_str.strip()

    # ⑧ 后端 + system prompt
    backend = get_backend(body.backend)
    if isinstance(backend, APIBackend):
        from app.ai.provider_presets import PROVIDERS
        preset = PROVIDERS.get(body.backend or "")
        cli_used = preset["name"] if preset else "自定义"
    else:
        cli_used = backend.command

    if not backend.is_available():
        return JSONResponse(
            {"detail": f"{cli_used} CLI 不可用，请确认已安装并配置"},
            status_code=503,
        )

    start = time.time()
    resp = await backend.ask(
        prompt=question,
        context=context_str,
        system_prompt=build_system_prompt(body.mode),
        work_dir=WORKSPACE_DIR,
    )
    trace.duration_ms = int((time.time() - start) * 1000)

    # 处理响应
    if resp.success and resp.content.strip():
        answer = resp.content
    elif resp.success and not resp.content.strip():
        logger.warning("QA CLI returned empty content (command=%s)", cli_used)
        answer = "抱歉，AI 服务返回了空内容，请确认 CLI 已登录并可用。"
    else:
        logger.warning("QA CLI error (command=%s): %s", cli_used, resp.error)
        answer = (
            "抱歉，AI 服务返回错误。"
            + ("（超时）" if "超时" in (resp.error or "") else "")
        )

    # 来源引用：只取实际进入上下文的条文（保证引用与上下文一致）
    sources = _extract_sources([it.clause for it, _ in picked])

    # 埋点
    _emit_trace(trace)

    return QAResponse(answer=answer, sources=sources, cli_used=cli_used)
