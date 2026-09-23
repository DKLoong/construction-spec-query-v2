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


def _local_model_dir() -> Path | None:
    """返回本地可用的模型目录（存在且含 config.json），无则 None。

    只做文件探测，不加载模型；加载路径与就绪探测共用同一判据，避免两处漂移。
    """
    for p in _LOCAL_MODEL_PATHS:
        if p.exists() and (p / "config.json").exists():
            return p
    return None


def get_reranker() -> "CrossEncoder | None":
    """懒加载 BGE-reranker-base 交叉编码器（单例）。优先本地路径，其次 HuggingFace 缓存。

    加载失败（含依赖未安装）后缓存失败态，后续调用直接返回 None、不再重试。
    """
    global _model
    if _model is None:
        try:
            from sentence_transformers import CrossEncoder

            # 优先使用本地已下载的模型
            local_dir = _local_model_dir()

            if local_dir:
                logger.info("从本地路径加载 CrossEncoder 模型: %s", local_dir)
                _model = CrossEncoder(str(local_dir))
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


def is_ready() -> bool:
    """探测精排模型是否就绪（**只探测，不加载**）。

    - 已加载（模型实例）→ True
    - 曾尝试且失败（False 哨兵）→ False（不再重试）
    - 未尝试（None）→ 只探测本地模型目录是否存在且含 config.json

    **绝不调用 `get_reranker()`**——未加载时它会真实例化模型（数秒），
    而本函数供健康检查等「每次打开都跑」的只读场景使用。
    注意：本探测只看本地 `models/` 目录，对仅存在于 HuggingFace 缓存中的模型
    会判为未就绪（分享场景的核心诉求正是「本地模型文件没带上」）。
    """
    if _model is not None:
        return _model is not False
    return _local_model_dir() is not None


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
