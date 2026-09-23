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
from app.qa.degrade import (
    RERANK_CE, RERANK_VECTOR, RERANK_NONE, rank_scores, resolve_thresholds,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# 记录本次请求实际生效的精排器（RERANK_CE / RERANK_VECTOR / RERANK_NONE），供阈值选型与埋点
_last_rerank_used = RERANK_NONE


# ── 精排降级链（CrossEncoder → bi-encoder 向量 → 原始顺序） ──

def _rerank_scored(question: str, candidates: list[dict]) -> list[tuple[dict, float]]:
    """精排打分，返回 (候选, 分数) 按分数降序全部候选（不在此截断条数）。

    - 候选 ≤ 1：直接返回 [(c, 1.0)]，不打分、不分层。
    - CrossEncoder 可用：分数 = 模型输出（量纲约 0~1）。
    - 降级 bi-encoder 向量：分数 = 余弦相似度（量纲 -1~1），阈值用 qa.vector.* 独立集。
    - 两者皆不可用：分数 = **排名归一化值**（无绝对相关度语义），
      阈值改为 min_score=0 + high_threshold=2/3，按排名切分强弱。
    """
    global _last_rerank_used
    if len(candidates) <= 1:
        _last_rerank_used = RERANK_NONE
        return [(c, 1.0) for c in candidates]

    texts = [(c.get("content") or "")[:300] for c in candidates]

    try:
        from app.ai.reranker import rerank
        scores = rerank(question, texts)
        if scores is not None:
            _last_rerank_used = RERANK_CE
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
        _last_rerank_used = RERANK_VECTOR
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [(c, float(s)) for c, s in ranked]
    except Exception as e:
        logger.warning("向量重排序失败，降级为原始顺序: %s", e)

    _last_rerank_used = RERANK_NONE
    # 修正（设计文档 §4.13 缺口 1）：不再返回全 1.0，改按排名归一化。
    # hybrid_search 的候选本身已按 RRF 分数降序，此处名次即相对相关度。
    return list(zip(candidates, rank_scores(len(candidates))))


def _extract_sources(clauses: list[dict]) -> list[dict]:
    """从实际进入上下文的条文提取去重引文来源（含 clause_id / status 供前端弹详情与高亮）"""
    seen = set()
    sources = []
    for r in clauses:
        key = (r.get("spec_code"), r.get("clause_no"))
        if key not in seen and key[0] and key[1]:
            seen.add(key)
            sources.append({
                "code": key[0], "clause_no": key[1],
                "clause_id": r.get("id"),
                "status": r.get("spec_status") or "",
                "replace_by_code": r.get("replace_by_code") or "",
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
    # 历史段【实际占用】token 与配置预算：build_history 是纯函数无 IO，
    # 超预算不留任何痕迹——故必须由调用方回填并在超支时告警（先可测，再调参）
    history_tokens: int = 0
    history_budget: int = 0
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
                    context_tokens, budget, dropped_overflow, context_empty,
                    history_tokens, history_budget, rerank_used, duration_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trace.question, trace.mode, trace.backend,
                    1 if trace.include_invalid else 0,
                    trace.rrf_total, trace.pool_size, trace.after_meta,
                    trace.after_threshold, trace.select_target,
                    trace.high_count, trace.low_count, trace.context_tokens,
                    trace.budget, trace.dropped_overflow,
                    1 if trace.context_empty else 0,
                    trace.history_tokens, trace.history_budget,
                    trace.rerank_used, trace.duration_ms,
                ),
            )
    except Exception as e:
        logger.warning("QA trace 落库失败: %s", e)


@dataclass
class QaContext:
    """一次问答的检索准备结果（/qa/ask 编排用）。

    抽出来是为了让后续的「放宽重发」（T13）与「流式输出」（T15）共用同一份
    检索逻辑——复制一份必然漂移。本 Task 只填 question / context_str / picked /
    trace / history_str / session_id；filtered_out 与 rerank_used 由 T13 补齐，
    故此处先留默认值（后续任务只做加法，不再重构本函数）。
    """
    question: str
    context_str: str
    picked: list
    trace: QATrace
    filtered_out: int = 0
    rerank_used: str = ""
    history_str: str = ""
    session_id: int | None = None


def _prepare_qa_context(question: str, body: QaRequest) -> QaContext:
    """检索 → 元数据过滤 → 精排 → 阈值过滤 → 动态条数 → 分层 → 上下文组装 + 会话/历史解析。

    纯准备阶段：不调用模型、不落库、不写埋点（埋点随本轮结果在 /qa/ask 收尾时输出）。
    """
    from app.qa.context import (
        filter_by_metadata, filter_by_score, dynamic_select,
        tier_items, build_context, build_history, estimate_tokens,
    )
    from app.qa.config import get_qa_float, get_qa_int, get_qa_str
    from app.qa import sessions as qa_sessions
    from app.search.hybrid_search import hybrid_search

    # 会话解析：不存在的 id 一律视为新会话——严格不跨会话取历史（D3）
    session_id = body.session_id
    if session_id is not None and qa_sessions.get_session(session_id) is None:
        logger.info("QA session_id=%s 不存在，按新会话处理", session_id)
        session_id = None
    # 历史段必须在落库本轮消息之前读取，否则本轮问答会被算进自己的历史
    history_messages = qa_sessions.get_messages(session_id) if session_id else []

    trace = QATrace(
        question=question[:100], mode=body.mode,
        backend=body.backend or "", include_invalid=body.include_invalid,
    )

    # ⓪ 历史段组装（纯函数无 IO）
    history_budget = get_qa_int("token.max_history_tokens")
    history_str = build_history(
        history_messages, get_qa_int("history.max_turns"), history_budget,
    )
    # 可观测性：超预算在服务端日志可见（build_history 逐轮丢弃最旧，但**最新一轮无条件保留**，
    # 故单轮自身超预算时历史段会突破预算——这是有意取舍，需要数据来定夺默认值）
    trace.history_tokens = estimate_tokens(history_str)
    trace.history_budget = history_budget
    if trace.history_tokens > history_budget:
        logger.warning(
            "QA 历史段超预算：history_tokens=%d > history_budget=%d",
            trace.history_tokens, trace.history_budget,
        )

    # ① RRF 混合召回（候选池大小走配置）
    pool = get_qa_int("retrieve.candidate_pool")
    keyword_mentions_non_clause = ("前言" in question) or ("条文说明" in question)
    has_dim = any([
        body.dim1_hierarchy, body.dim1_industry, body.dim1_nature, body.dim2_stage,
        body.dim3_usage,
        body.dim4_specialty, body.dim5_location, body.dim6_material,
    ])
    sq = SearchQuery(
        keyword=question, per_page=pool,
        dim1_hierarchy=body.dim1_hierarchy, dim1_industry=body.dim1_industry,
        dim1_nature=body.dim1_nature,
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
    # status_filter 三分支：
    #   None（缺参，旧客户端）→ settings 默认白名单 + include_invalid（旧语义，过滤废止）
    #   ""（显式全不勾）    → 放行非现行（eff_include_invalid=True）
    #   非空                 → 覆盖默认白名单 + include_invalid
    if body.status_filter is None:
        status_allow = tuple(
            s.strip() for s in get_qa_str("meta.status_allow").split(",") if s.strip()
        )
        eff_include_invalid = body.include_invalid
    elif body.status_filter.strip():
        status_allow = tuple(
            s.strip() for s in body.status_filter.split(",") if s.strip()
        )
        eff_include_invalid = body.include_invalid
    else:
        status_allow = tuple(
            s.strip() for s in get_qa_str("meta.status_allow").split(",") if s.strip()
        )
        eff_include_invalid = True  # 显式全不勾 → 不过滤状态
    candidates = filter_by_metadata(
        candidates, eff_include_invalid, status_allow=status_allow,
    )
    trace.after_meta = len(candidates)

    # ③④ 精排打分 + 分数阈值过滤（阈值按实际生效的精排级别解析，见 app/qa/degrade.py）
    ranked = _rerank_scored(question, candidates)
    trace.rerank_used = _last_rerank_used
    min_score, high_thr = resolve_thresholds(_last_rerank_used, get_qa_float)
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

    # 历史段置于条文上下文之前（system prompt → 历史 → 本轮条文 → 本轮问题）
    if history_str:
        context_str = f"{history_str}\n\n{context_str}"

    return QaContext(question=question, context_str=context_str, picked=picked,
                     trace=trace, history_str=history_str, session_id=session_id)


@router.post("/qa/ask")
async def qa_ask(request: Request, body: QaRequest):
    """AI 问答：检索 → 元数据过滤 → 精排 → 阈值过滤 → 动态条数 → 分层 → Token 兜底 → 推理

    会话为惰性创建（session_id 为 None 时新建），成功轮次落库供下轮做历史。
    """
    from app.ai.prompts import build_system_prompt
    from app.ai.cli_client import get_backend
    from app.qa import sessions as qa_sessions
    from app.config import WORKSPACE_DIR

    question = body.question.strip()
    if not question:
        return JSONResponse({"detail": "问题不能为空"}, status_code=400)

    ctx = _prepare_qa_context(question, body)
    trace = ctx.trace

    # ⑧ 后端 + system prompt（多轮历史存在时追加引用护栏）
    backend = get_backend(body.backend)
    if isinstance(backend, APIBackend):
        from app.ai.provider_presets import PROVIDERS
        preset = PROVIDERS.get(body.backend or "")
        cli_used = preset["name"] if preset else "自定义"
        if not trace.backend:
            trace.backend = cli_used  # 前端显式预设未传时回填实际名，供 QA 日志展示
    else:
        cli_used = backend.command
        if not trace.backend:
            # 默认走 CLI（body.backend 为空）：QA 日志回填可读命令名
            trace.backend = (backend.command or "cli").replace("\\", "/").rsplit("/", 1)[-1]

    if not backend.is_available():
        return JSONResponse(
            {"detail": f"{cli_used} CLI 不可用，请确认已安装并配置"},
            status_code=503,
        )

    start = time.time()
    resp = await backend.ask(
        prompt=question,
        context=ctx.context_str,
        system_prompt=build_system_prompt(body.mode, multi_turn=bool(ctx.history_str)),
        work_dir=WORKSPACE_DIR,
    )
    trace.duration_ms = int((time.time() - start) * 1000)

    # 来源引用：只取实际进入上下文的条文（保证引用与上下文一致）
    sources = _extract_sources([it.clause for it, _ in ctx.picked])

    # 易混淆术语命中：检测对象恒为用户问题原文（仅提示，不做任何改写）
    confusable_hits = []
    if question:
        from app.lexicon import store, confusable
        confusable_hits = confusable.detect_confusable(
            question, store.load_confusable_pairs())

    # 处理响应
    persist_ok = False
    if resp.success and resp.content.strip():
        answer = resp.content
        persist_ok = True
    elif resp.success:
        logger.warning("QA CLI returned empty content (command=%s)", cli_used)
        answer = "抱歉，AI 服务返回了空内容，请确认 CLI 已登录并可用。"
    else:
        logger.warning("QA CLI error (command=%s): %s", cli_used, resp.error)
        answer = (
            "抱歉，AI 服务返回错误。"
            + ("（超时）" if "超时" in (resp.error or "") else "")
        )

    # 仅成功轮次落库（失败不入库，避免半截会话污染历史）
    session_id = ctx.session_id
    if persist_ok:
        if session_id is None:
            session_id = qa_sessions.create_session(
                qa_sessions.derive_title(question))
        qa_sessions.append_message(session_id, "user", question)
        qa_sessions.append_message(
            session_id, "assistant", answer, sources=sources,
            confusable=confusable_hits, mode=body.mode,
        )

    # 埋点
    _emit_trace(trace)

    return QAResponse(answer=answer, sources=sources, cli_used=cli_used,
                      confusable_hits=confusable_hits,
                      rerank_used=trace.rerank_used, session_id=session_id or 0)
