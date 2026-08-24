"""CrossEncoder 交叉编码器精排（QA 精排首选）"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_model: Optional[object] = None

# 本地模型路径（优先使用本地已下载的模型，避免联网）
_LOCAL_MODEL_PATHS = [
    Path(__file__).resolve().parent.parent.parent / "models" / "BAAI" / "bge-reranker-base",
]


def get_reranker():
    """懒加载 BGE-reranker-base 交叉编码器（单例）。优先本地路径，其次 HuggingFace 缓存。"""
    global _model
    if _model is None:
        try:
            from sentence_transformers import CrossEncoder

            # 优先使用本地已下载的模型
            model_path = None
            for p in _LOCAL_MODEL_PATHS:
                if p.exists() and (p / "config.json").exists():
                    model_path = str(p)
                    break

            if model_path:
                logger.info("从本地路径加载 CrossEncoder 模型: %s", model_path)
                _model = CrossEncoder(model_path)
            else:
                # 回退到 HuggingFace 缓存（local_files_only=True，不触发网络下载）
                _model = CrossEncoder(
                    "BAAI/bge-reranker-base",
                    local_files_only=True,
                )
            logger.info("BGE-reranker-base 模型加载完成")
        except ImportError:
            logger.warning("sentence-transformers 未安装，CrossEncoder 精排不可用")
            _model = False
        except Exception as e:
            logger.warning("CrossEncoder 模型加载失败: %s", e)
            _model = False
    return _model if _model is not False else None


def rerank(question: str, texts: list[str]) -> list[float] | None:
    """用 CrossEncoder 对 (问题, 条文) 对打分精排。

    - texts 为空时返回 []（无需模型）
    - 模型不可用时返回 None（由上层回退 bi-encoder 向量重排序）
    - 模型可用时返回与 texts 顺序对齐的分数列表
    """
    if not texts:
        return []
    model = get_reranker()
    if model is None:
        return None
    pairs = [(question, t) for t in texts]
    scores = model.predict(pairs)
    return scores.tolist()
