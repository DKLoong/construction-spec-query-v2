"""AI 问答路由"""
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.models import QaRequest, QAResponse, SearchQuery
from app.ai.api_client import APIBackend

logger = logging.getLogger(__name__)
router = APIRouter()

# 上下文构建参数
_CONTEXT_MAX_RESULTS = 5       # 送入 AI 的最多条文数
_CONTEXT_MAX_CHARS = 200        # 每条条文最多截取字符数
_CANDIDATE_POOL_SIZE = 30       # 初筛候选池大小（供重排序）


def _build_context(results: list[dict], max_chars: int = _CONTEXT_MAX_CHARS) -> str:
    """将搜索结果格式化为 AI 上下文字符串"""
    if not results:
        return ""
    lines = []
    for r in results:
        code = r.get("spec_code", "?")
        clause_no = r.get("clause_no", "?")
        content = (r.get("content") or "")[:max_chars]
        lines.append(f"[{code} {clause_no}] {content}")
    return "\n".join(lines)


def _extract_sources(results: list[dict]) -> list[dict]:
    """从搜索结果提取去重的引文来源"""
    seen = set()
    sources = []
    for r in results:
        key = (r.get("spec_code"), r.get("clause_no"))
        if key not in seen and key[0] and key[1]:
            seen.add(key)
            sources.append({"code": key[0], "clause_no": key[1]})
    return sources


def _rerank_by_vector(question: str, candidates: list[dict],
                      top_k: int = _CONTEXT_MAX_RESULTS) -> list[dict]:
    """向量重排序：用 BGE 模型编码问题+条文，余弦相似度排序取 Top-K"""
    if len(candidates) <= top_k:
        return candidates

    try:
        from app.ai.embedding import embed_texts
        import numpy as np

        # 编码问题
        q_vec = np.array(embed_texts([question])[0], dtype=np.float32)

        # 批量编码所有候选条文
        texts = [(c.get("content") or "")[:300] for c in candidates]
        emb = np.array(embed_texts(texts), dtype=np.float32)

        # 余弦相似度（嵌入已归一化，点积即余弦相似度）
        scores = np.dot(emb, q_vec)

        # 按相似度降序排列
        ranked = sorted(
            zip(candidates, scores), key=lambda x: x[1], reverse=True
        )
        return [c for c, _ in ranked[:top_k]]
    except Exception as e:
        logger.warning("向量重排序失败，降级为原始顺序: %s", e)
        return candidates[:top_k]


@router.post("/qa/ask")
async def qa_ask(request: Request, body: QaRequest):
    """AI 问答：检索 → 向量重排序 → 精简上下文 → CLI 推理 → 返回答案"""
    from app.search.hybrid_search import hybrid_search
    from app.ai.cli_client import get_backend
    from app.config import WORKSPACE_DIR

    question = body.question.strip()
    if not question:
        return JSONResponse({"detail": "问题不能为空"}, status_code=400)

    # 1. 宽泛检索（候选池 > 最终送入数）
    sq = SearchQuery(keyword=question, per_page=_CANDIDATE_POOL_SIZE)
    try:
        candidates, _ = hybrid_search(sq)
    except Exception as e:
        logger.error("QA hybrid_search failed: %s", e)
        candidates = []

    # 2. 向量重排序 → 取最相关的 Top-K
    results = _rerank_by_vector(question, candidates)

    # 3. 构建精简上下文
    context_str = _build_context(results)

    # 4. 选择后端
    backend = get_backend(body.backend)
    if isinstance(backend, APIBackend):
        from app.ai.provider_presets import PROVIDERS
        preset = PROVIDERS.get(body.backend)
        cli_used = preset["name"] if preset else "自定义"
    else:
        cli_used = backend.command

    if not backend.is_available():
        return JSONResponse(
            {"detail": f"{cli_used} CLI 不可用，请确认已安装并配置"},
            status_code=503,
        )

    # 5. 调用 CLI
    resp = await backend.ask(
        prompt=question,
        context=context_str,
        work_dir=WORKSPACE_DIR,
    )

    # 6. 处理响应
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

    sources = _extract_sources(results)

    return QAResponse(answer=answer, sources=sources, cli_used=cli_used)
