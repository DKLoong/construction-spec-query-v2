import logging
from app.ai.cli_client import get_backend
from app.classifier.batch_queue import (
    get_pending_batch, apply_ai_results, collect_label_candidates,
)

logger = logging.getLogger(__name__)

DIMS = ["dim4", "dim5", "dim6"]


def process_pending_batches(backend_name: str | None = None, force: bool = False,
                            drain: bool = False) -> int:
    """处理所有维度的待分类批次，返回处理条数。

    drain=True（手动「运行 AI 分类」用）：每维内部按 batch_size 循环取批，直到该维
    待处理清空再切下一维——一次点击清空 backlog。默认 drain=False 保持「每维只跑一批」
    语义（spec 批量重跑等调用不受影响）。每批失败仅记日志并停止该维，避免卡死循环。
    """
    backend = get_backend(backend_name)
    if not backend.is_available():
        logger.warning(f"CLI ({backend_name}) 不可用，跳过批次处理")
        return 0

    total = 0
    for dim in DIMS:
        while True:
            batch = get_pending_batch(dim, force=force)
            if not batch:
                break

            try:
                candidate_labels = collect_label_candidates(dim)
                results = backend.classify_batch_sync(batch, dim, candidate_labels)
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
                # 本批失败：条目已置 ai_processing，不再被取批；停本维防死循环
                break
            if not drain:
                break

    return total
