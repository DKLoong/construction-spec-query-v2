"""检索/问答共用精排降级链：CrossEncoder → bi-encoder 向量 → 原始顺序

从 QA 的 `_rerank_scored` 抽取出的纯函数，供检索模块（hybrid_search）与
QA 模块共同调用，避免两处复制同一套降级逻辑。
"""

import logging

logger = logging.getLogger(__name__)


def rerank_candidates(question: str, candidates: list[dict]) -> tuple[list[tuple[dict, float]], str]:
    """对候选条文精排打分，返回 (按分数降序的全部候选, 实际使用档位)。

    - 档位：`"crossencoder"`（CrossEncoder）／`"vector"`（bi-encoder 余弦）／`"none"`（原始顺序）
    - 候选 ≤ 1：直接返回原序 [(c, 1.0)]，不打分、不分层。
    - CrossEncoder 可用：分数 = 模型输出（量纲约 0~1）。
    - 降级 bi-encoder 向量：分数 = 余弦相似度（量纲 -1~1）。
    - 两者都失败：保持调用方传入顺序，分数统一 1.0。

    本函数只负责排序打分，不截断条数（截断在调用方编排层按各自需求决定）。
    """
    if len(candidates) <= 1:
        return [(c, 1.0) for c in candidates], "none"

    texts = [(c.get("content") or "")[:300] for c in candidates]

    try:
        from app.ai.reranker import rerank
        scores = rerank(question, texts)
        if scores is not None:
            ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
            return [(c, float(s)) for c, s in ranked], "crossencoder"
    except Exception as e:
        logger.warning("CrossEncoder 精排异常: %s", e)

    try:
        from app.ai.embedding import embed_texts
        import numpy as np
        q_vec = np.array(embed_texts([question])[0], dtype=np.float32)
        emb = np.array(embed_texts(texts), dtype=np.float32)
        scores = np.dot(emb, q_vec)
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [(c, float(s)) for c, s in ranked], "vector"
    except Exception as e:
        logger.warning("向量重排序失败，降级为原始顺序: %s", e)

    return [(c, 1.0) for c in candidates], "none"
