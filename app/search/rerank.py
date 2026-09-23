"""检索侧精排降级链：CrossEncoder → bi-encoder 向量 → 原始顺序

从 QA 的 `_rerank_scored` 抽取出的纯函数，**实际只被检索模块调用**
（`app/search/hybrid_search.py` 一处）。QA 侧并未改为调用本函数，而是保留了
自己的同逻辑副本 `app/routes/qa_routes._rerank_scored`——两处差异：
  1. QA 副本需要**精排级别**（RERANK_CE / RERANK_VECTOR / RERANK_NONE 常量，
     供 `app/qa/degrade.resolve_thresholds` 按级别选阈值），故返回 (候选, 分数)
     而不返回级别字符串；检索侧丢弃本函数返回的级别。
  2. 两处不可用时都回退，但**分数不同**：检索侧统一 1.0（不分层，全 1.0 无害），
     QA 侧用 `degrade.rank_scores` 的排名归一化值（QA 需要按排名切分强弱）。
两处逻辑的合并见 TODOS T18。
"""

import logging

from app.ai.text_clean import plain_text

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

    # content 是渲染载荷（含 OCR 的 HTML 表格/LaTeX），先清洗再截断：
    # 否则表格条文的前 300 字符几乎全是标记，正文被截掉、模型只看到「td style」。
    texts = [plain_text(c.get("content") or "")[:300] for c in candidates]

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
