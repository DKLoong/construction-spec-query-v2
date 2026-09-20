"""CrossEncoder 交叉编码器精排（QA 精排首选）"""
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

if TYPE_CHECKING:  # 仅供类型检查；运行时按需 import，避免硬依赖
    from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)
# 三态懒加载缓存：None=未尝试 / 模型实例=已加载 / False=尝试过且失败（不再重试）
_model: Optional[object] = None

# 本地模型路径（优先使用本地已下载的模型，避免联网）
_LOCAL_MODEL_PATHS = [
    Path(__file__).resolve().parent.parent.parent / "models" / "BAAI" / "bge-reranker-base",
]


def get_reranker() -> "CrossEncoder | None":
    """懒加载 BGE-reranker-base 交叉编码器（单例）。优先本地路径，其次 HuggingFace 缓存。

    加载失败（含依赖未安装）后缓存失败态，后续调用直接返回 None、不再重试。
    """
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
    # 三态哨兵（None / 实例 / False）类型检查器无法窄化，在**出口边界**显式 cast
    # 表达对外契约 `CrossEncoder | None`（内部表示不变，既有测试与行为完全不受影响）。
    return cast("CrossEncoder | None", _model if _model is not False else None)


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
