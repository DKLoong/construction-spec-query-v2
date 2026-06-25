"""AI 问答路由"""
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.models import QaRequest, QAResponse, SearchQuery

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_context(results: list[dict], max_chars: int = 500) -> str:
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


@router.post("/qa/ask")
async def qa_ask(request: Request, body: QaRequest):
    """AI 问答：检索相关条文 → CLI 推理 → 返回答案"""
    from app.search.hybrid_search import hybrid_search
    from app.ai.cli_client import get_backend
    from app.config import WORKSPACE_DIR

    question = body.question.strip()
    if not question:
        return JSONResponse({"detail": "问题不能为空"}, status_code=400)

    # 1. 检索相关条文
    sq = SearchQuery(keyword=question, per_page=10)
    try:
        results, _ = hybrid_search(sq)
    except Exception as e:
        logger.error("QA hybrid_search failed: %s", e)
        results, _ = [], 0

    # 2. 构建上下文
    context_str = _build_context(results)

    # 3. 选择后端
    backend = get_backend(body.backend)
    cli_used = backend.command

    if not backend.is_available():
        return JSONResponse(
            {"detail": f"{cli_used} CLI 不可用，请确认已安装并配置"},
            status_code=503,
        )

    # 4. 调用 CLI
    resp = await backend.ask(
        prompt=question,
        context=context_str,
        work_dir=WORKSPACE_DIR,
    )

    # 5. 处理响应
    if resp.success:
        answer = resp.content
    else:
        logger.warning("QA CLI error (command=%s): %s", cli_used, resp.error)
        answer = (
            "抱歉，AI 服务返回错误。"
            + ("（超时）" if "超时" in (resp.error or "") else "")
        )

    sources = _extract_sources(results)

    return QAResponse(answer=answer, sources=sources, cli_used=cli_used)
