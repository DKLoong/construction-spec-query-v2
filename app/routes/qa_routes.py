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
from app.models import (
    QaRequest, QAResponse, QaSessionRenameRequest, SearchQuery, QA_DIM_FIELDS,
)
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


# ── 输出形态辅助（两种形态共用） ──

def _sse(event: str, data: dict) -> str:
    """构造一条 SSE 消息（event + data 各一行，以空行结束）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_backend(backend_name: str | None, trace: QATrace):
    """解析后端并回填可读名到埋点，返回 (backend, cli_used)。"""
    from app.ai.cli_client import get_backend
    backend = get_backend(backend_name)
    if isinstance(backend, APIBackend):
        from app.ai.provider_presets import PROVIDERS
        preset = PROVIDERS.get(backend_name or "")
        cli_used = preset["name"] if preset else "自定义"
    else:
        # CLI 命令可能是完整路径，埋点只记可读的命令名
        cli_used = (backend.command or "cli").replace("\\", "/").rsplit("/", 1)[-1]
    if not trace.backend:
        trace.backend = cli_used
    return backend, cli_used


def _answer_text(resp, cli_used: str) -> str:
    """把后端响应归一为给用户看的文本（失败/空内容给可读提示）。"""
    if resp.success and resp.content.strip():
        return resp.content
    if resp.success:
        logger.warning("QA 后端返回空内容 (command=%s)", cli_used)
        return "抱歉，AI 服务返回了空内容，请确认后端已正确配置。"
    logger.warning("QA 后端错误 (command=%s): %s", cli_used, resp.error)
    return "抱歉，AI 服务返回错误。" + ("（超时）" if "超时" in (resp.error or "") else "")


@dataclass
class QaContext:
    """一次问答的检索准备结果（非流式与流式共用）。

    抽出它是为了让两种输出形态共用同一份检索逻辑——重复一份必然漂移
    （首版设计已因此漏掉埋点、并在错误位置读全局 rerank_used 造成竞态）。

    `rerank_used` 是**准备阶段从 `_last_rerank_used` 立即拷贝**的值，供两条输出
    路径（`_qa_json` 的 `QAResponse.rerank_used`、`_sse_stream` 的 `done` 载荷）
    读取——**不得**在跨 `await` 的函数里回读模块级全局（并发请求会覆盖它）。
    该拷贝与 `trace.rerank_used` **同源同值**（两者都由同一个局部变量赋值），
    否则响应与落库埋点会各说一套。
    `filtered_out` 由 T13 补齐、`rerank_used` 由 T15 补齐；两者都不给默认值，
    使「忘接线」成为构造期的硬错误，而不是静默的 0 / ""。
    """
    question: str
    context_str: str
    picked: list
    trace: QATrace
    filtered_out: int
    rerank_used: str
    history_str: str
    session_id: int | None


def _effective_filters(body: QaRequest) -> dict:
    """本轮实际生效的筛选（分类维度 + **显式**状态过滤 + 前言放行）。

    用途：① 随响应返回，供前端显示「本轮生效筛选」——「回复中切换筛选只影响下一轮」
    这件事必须可见，否则用户切了会以为立即生效；② T15 起随助手消息落库追溯（D5）。

    放宽（relaxed）时分类维度为空——那正是放宽的语义。
    维度字段名取自 QA_DIM_FIELDS（单一来源），新增维度时不会漏记。
    状态过滤判 `is not None` 而非真值，两种语义必须分开：
      None（缺参，旧客户端）→ 走 settings 默认白名单，**不记录**（记了等于把默认值冒充用户选择）；
      ""（显式全不勾）      → 放行非现行，**是**用户选择，记录为 ""。
    「前言放行」：只记**复选框显式勾选**（body.include_non_clause）——问题文本里
    出现「前言」字样属于隐式兜底，不是用户对本轮筛选的选择，记进去会让回看时
    「这条答案在什么筛选下产生」失真。
    """
    out: dict = {}
    if not body.relaxed:
        out = {k: getattr(body, k) for k in QA_DIM_FIELDS if getattr(body, k)}
    if body.status_filter is not None:
        out["status_filter"] = body.status_filter
    # ↓↓ 本 Task 追加的**唯一**一行逻辑（body.include_non_clause 在本 Task 才声明）↓↓
    if body.include_non_clause:
        out["include_non_clause"] = True
    return out


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
    # 放行非条文：左栏复选框显式开关 **或** 问题文本兜底
    # （「问题文本含前言/条文说明字样时隐式放行」是设计文档 §4.3 的兜底条款）
    include_non_clause = (
        body.include_non_clause
        or ("前言" in question) or ("条文说明" in question)
    )
    # 放宽（relaxed）时清空分类维度：那正是「放宽」的语义（状态过滤与前言设置仍生效）
    dims = {} if body.relaxed else {k: getattr(body, k) for k in QA_DIM_FIELDS if getattr(body, k)}
    has_dim = any(dims.values())
    sq = SearchQuery(
        keyword=question, per_page=pool,
        include_non_clause=include_non_clause, **dims,
    )
    try:
        candidates, total = hybrid_search(sq)
    except Exception as e:
        logger.error("QA hybrid_search failed: %s", e)
        candidates, total = [], 0
    trace.rrf_total = total
    trace.pool_size = len(candidates)

    # 分类筛选候选不足：**不再静默放宽**（设计文档 D9）。
    # 原因：QA 与检索结果同屏后，静默放宽会造成"左边筛了分类、
    # 右边答案却来自别的分类"的可见不一致。改为如实报告候选量，
    # 由前端提示并提供一键放宽（body.relaxed=True 重发）。
    filtered_out = 0
    if not body.relaxed and has_dim and len(candidates) < get_qa_int("retrieve.qa_min_candidates"):
        wide_sq = SearchQuery(
            keyword=question, per_page=pool,
            include_non_clause=include_non_clause,
        )
        try:
            _, wide_total = hybrid_search(wide_sq)
            filtered_out = wide_total
            logger.info("QA 分类筛选候选不足（%d 条），已如实报告全局命中 %d 条",
                        len(candidates), wide_total)
        except Exception as e:
            logger.error("QA 分类筛选候选不足的诊断检索失败: %s", e)

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
    # 立即拷贝：流式路径跨多次 await，期间并发请求会改写模块级 _last_rerank_used。
    # 拷贝值与 trace 同源同值（同一个局部变量），保证「响应」与「落库埋点」不各说一套。
    rerank_used = _last_rerank_used
    trace.rerank_used = rerank_used
    min_score, high_thr = resolve_thresholds(rerank_used, get_qa_float)
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
                     trace=trace, filtered_out=filtered_out,
                     rerank_used=rerank_used,
                     history_str=history_str, session_id=session_id)


def _confusable_hits(question: str) -> list[dict]:
    """易混淆术语命中：检测对象恒为用户问题原文（仅提示，不做任何改写）。"""
    if not question:
        return []
    from app.lexicon import store, confusable
    return confusable.detect_confusable(question, store.load_confusable_pairs())


def _finish_turn(ctx: QaContext, answer: str, persist_ok: bool,
                 body: QaRequest) -> tuple[list[dict], list[dict], int | None]:
    """收尾（两条路径共用）：提取来源、检测易混淆、成功则落库、写埋点。

    返回 (sources, confusable_hits, session_id)。失败轮次不入库（避免半截会话）。
    """
    from app.qa import sessions as qa_sessions

    # 来源引用：只取实际进入上下文的条文（保证引用与上下文一致）
    sources = _extract_sources([it.clause for it, _ in ctx.picked])
    confusable_hits = _confusable_hits(ctx.question)
    session_id = ctx.session_id
    if persist_ok:
        if session_id is None:
            session_id = qa_sessions.create_session(
                qa_sessions.derive_title(ctx.question))
        qa_sessions.append_message(session_id, "user", ctx.question)
        # 助手消息记录**当轮实际生效的筛选**（D5）——回看历史时据此还原
        # 「这条答案是在什么筛选下产生的」。筛选不入库则该信息不可逆丢失。
        qa_sessions.append_message(
            session_id, "assistant", answer,
            sources=sources, confusable=confusable_hits,
            filters=_effective_filters(body), mode=body.mode)
    _emit_trace(ctx.trace)
    return sources, confusable_hits, session_id


@router.post("/qa/ask")
async def qa_ask(request: Request, body: QaRequest):
    """AI 问答。

    默认返回 JSON；body.stream=True 时返回 SSE（text/event-stream）。
    两种输出形态共用 _prepare_qa_context，检索逻辑只有一份。

    会话为惰性创建（session_id 为 None 时新建），成功轮次落库供下轮做历史。
    """
    question = body.question.strip()
    if not question:
        return JSONResponse({"detail": "问题不能为空"}, status_code=400)

    ctx = _prepare_qa_context(question, body)

    if body.stream:
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            _sse_stream(ctx, body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return await _qa_json(ctx, body)


async def _qa_json(ctx: QaContext, body: QaRequest):
    """非流式输出：一次推理 → 收尾落库/埋点 → 完整 JSON。"""
    from app.ai.prompts import build_system_prompt
    from app.config import WORKSPACE_DIR

    backend, cli_used = _resolve_backend(body.backend, ctx.trace)
    if not backend.is_available():
        return JSONResponse({"detail": f"{cli_used} 不可用，请确认已配置"},
                            status_code=503)

    start = time.time()
    resp = await backend.ask(
        prompt=ctx.question, context=ctx.context_str,
        system_prompt=build_system_prompt(body.mode, multi_turn=bool(ctx.history_str)),
        work_dir=WORKSPACE_DIR,
    )
    ctx.trace.duration_ms = int((time.time() - start) * 1000)

    persist_ok = bool(resp.success and resp.content.strip())
    answer = _answer_text(resp, cli_used)
    sources, confusable_hits, session_id = _finish_turn(ctx, answer, persist_ok, body)

    return QAResponse(answer=answer, sources=sources, cli_used=cli_used,
                      confusable_hits=confusable_hits,
                      rerank_used=ctx.rerank_used, session_id=session_id or 0,
                      filtered_out=ctx.filtered_out,
                      effective_filters=_effective_filters(body))


async def _sse_stream(ctx: QaContext, body: QaRequest):
    """流式输出：stage×N → delta×N → done | error。

    注意：rerank_used 取自 ctx（准备阶段已拷贝），**不得**在此处再读
    模块级 _last_rerank_used——本函数跨多次 await，期间并发请求会改写它。
    """
    from app.ai.prompts import build_system_prompt
    from app.config import WORKSPACE_DIR

    yield _sse("stage", {"stage": "retrieving"})
    yield _sse("stage", {"stage": "reranking"})

    backend, cli_used = _resolve_backend(body.backend, ctx.trace)
    if not backend.is_available():
        yield _sse("error", {"message": f"{cli_used} 不可用，请确认已配置"})
        return

    yield _sse("stage", {"stage": "generating"})

    parts: list[str] = []
    start = time.time()
    async for ev in backend.ask_stream(
        prompt=ctx.question, context=ctx.context_str,
        system_prompt=build_system_prompt(body.mode, multi_turn=bool(ctx.history_str)),
        work_dir=WORKSPACE_DIR,
    ):
        if ev["type"] == "delta":
            parts.append(ev["text"])
            yield _sse("delta", {"text": ev["text"]})
        elif ev["type"] == "error":
            ctx.trace.duration_ms = int((time.time() - start) * 1000)
            yield _sse("error", {"message": ev.get("message") or "AI 服务返回错误"})
            _emit_trace(ctx.trace)      # 失败也埋点，供排查
            return
    ctx.trace.duration_ms = int((time.time() - start) * 1000)

    answer = "".join(parts)
    sources, confusable_hits, session_id = _finish_turn(
        ctx, answer, bool(answer.strip()), body)

    yield _sse("done", {
        "session_id": session_id or 0,
        "sources": sources,
        "confusable_hits": confusable_hits,
        "rerank_used": ctx.rerank_used,      # 拷贝值，非全局
        "filtered_out": ctx.filtered_out,
        "effective_filters": _effective_filters(body),   # 前端显示「本轮生效筛选」（D5）
    })


# ── 会话管理接口 ──

@router.get("/qa/sessions")
async def qa_list_sessions():
    """会话列表（按最近活跃倒序）。"""
    from app.qa import sessions as qa_sessions
    return JSONResponse({"sessions": qa_sessions.list_sessions()})


@router.get("/qa/sessions/{session_id}")
async def qa_get_session(session_id: int):
    """会话详情：元信息 + 全部消息（含 sources，供前端重建条文链接）。"""
    from app.qa import sessions as qa_sessions
    sess = qa_sessions.get_session(session_id)
    if sess is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    return JSONResponse({
        "session": sess,
        "messages": qa_sessions.get_messages(session_id),
    })


# 会话标题长度上限（业务常量集中管理）
_SESSION_TITLE_MAX = 100


@router.patch("/qa/sessions/{session_id}")
async def qa_rename_session(session_id: int, body: QaSessionRenameRequest):
    """重命名会话；空白标题拒绝，超长截断。"""
    from app.qa import sessions as qa_sessions
    title = (body.title or "").strip()
    if not title:
        return JSONResponse({"detail": "会话名不能为空"}, status_code=400)
    if qa_sessions.get_session(session_id) is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    qa_sessions.rename_session(session_id, title[:_SESSION_TITLE_MAX])
    return JSONResponse({"ok": True})


@router.delete("/qa/sessions/{session_id}")
async def qa_delete_session(session_id: int):
    """删除会话及其全部消息（前端需二次确认）。"""
    from app.qa import sessions as qa_sessions
    if not qa_sessions.delete_session(session_id):
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    return JSONResponse({"ok": True})


@router.get("/qa/sessions/{session_id}/export")
async def qa_export_session(session_id: int):
    """导出会话为 Markdown 附件。"""
    from urllib.parse import quote

    from fastapi.responses import Response
    from app.qa import sessions as qa_sessions

    sess = qa_sessions.get_session(session_id)
    if sess is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    md = qa_sessions.build_markdown(sess, qa_sessions.get_messages(session_id))
    # 文件名做 RFC 5987 编码，避免中文标题导致下载名乱码
    fname = quote(f"{sess['title']}.md")
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@router.get("/qa/search")
async def qa_search_messages(q: str = ""):
    """跨会话搜索消息内容（LIKE，参数化 + 通配符转义）。

    只读接口：查询一律委托 `sessions.search_messages`（T5），路由内不拼任何 SQL，
    也不重复它已有的处理（空词早退与 strip 都在 T5 里，见 sessions.py:253-255）。
    返回结构恒为 `{"hits": [...]}`——空关键词与无命中都是 `[]`，不返回 null。
    """
    from app.qa import sessions as qa_sessions

    return JSONResponse({"hits": qa_sessions.search_messages(q)})
