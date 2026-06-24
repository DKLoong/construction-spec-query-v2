import logging
from app.ai.cli_client import get_backend
from app.classifier.batch_queue import get_pending_batch, apply_ai_results

logger = logging.getLogger(__name__)

DIMS = ["dim4", "dim5", "dim6"]


def process_pending_batches(backend_name: str = "claude", force: bool = False) -> int:
    """处理所有维度的待分类批次，返回处理条数。"""
    backend = get_backend(backend_name)
    if not backend.is_available():
        logger.warning(f"CLI ({backend_name}) 不可用，跳过批次处理")
        return 0

    total = 0
    for dim in DIMS:
        batch = get_pending_batch(dim, force=force)
        if not batch:
            continue

        try:
            results = backend.classify_batch_sync(batch, dim)
            if results:
                formatted = [
                    {"clause_id": r.clause_id, "label": r.label, "confidence": r.confidence}
                    for r in results
                ]
                batch_id = batch[0].get("batch_id")
                if batch_id:
                    apply_ai_results(batch_id, formatted)
                    total += len(formatted)
        except Exception as e:
            logger.error(f"批次处理失败 (dim={dim}): {e}")

    return total
