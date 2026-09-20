import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

if TYPE_CHECKING:  # 仅供类型检查；运行时按需 import，避免硬依赖
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)
# 三态懒加载缓存：None=未尝试 / 模型实例=已加载 / False=尝试过且失败（不再重试）
_model: Optional[object] = None

# 本地模型路径（优先使用 ModelScope 下载的，其次 HuggingFace 缓存）
_LOCAL_MODEL_PATHS = [
    Path(__file__).resolve().parent.parent.parent / "models" / "BAAI" / "bge-small-zh-v1.5",
    Path(__file__).resolve().parent.parent.parent / "models" / "BAAI" / "bge-small-zh-v1___5",
]


def get_model() -> "SentenceTransformer | None":
    """懒加载 BGE-small-zh 模型（单例）。优先本地路径，其次 HuggingFace 缓存。

    加载失败（含依赖未安装）后缓存失败态，后续调用直接返回 None、不再重试。
    """
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            # 优先使用本地已下载的模型
            model_path = None
            for p in _LOCAL_MODEL_PATHS:
                if p.exists() and (p / "config.json").exists():
                    model_path = str(p)
                    break

            if model_path:
                logger.info(f"从本地路径加载 BGE 模型: {model_path}")
                _model = SentenceTransformer(model_path)
            else:
                # 回退到 HuggingFace 缓存（local_files_only=True）
                _model = SentenceTransformer(
                    "BAAI/bge-small-zh-v1.5",
                    local_files_only=True
                )
            logger.info("BGE-small-zh 模型加载完成")
        except ImportError:
            logger.warning("sentence-transformers 未安装，向量检索不可用")
            _model = False
        except Exception as e:
            logger.warning(f"BGE 模型加载失败（首次使用需手动下载: "
                           f"python -c \"from sentence_transformers import SentenceTransformer; "
                           f"SentenceTransformer('BAAI/bge-small-zh-v1.5')\"）: {e}")
            _model = False
    # 三态哨兵（None / 实例 / False）类型检查器无法窄化，在**出口边界**显式 cast
    # 表达对外契约 `SentenceTransformer | None`（内部表示不变，既有测试与行为完全不受影响）。
    return cast("SentenceTransformer | None", _model if _model is not False else None)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """将文本列表转为 512 维向量"""
    if not texts:
        return []
    model = get_model()
    if model is None:
        raise RuntimeError("Embedding 模型不可用")
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()
