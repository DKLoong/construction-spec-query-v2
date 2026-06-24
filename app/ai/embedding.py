import logging
from typing import Optional

logger = logging.getLogger(__name__)
_model: Optional[object] = None


def get_model():
    """懒加载 BGE-small-zh 模型（单例）"""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
            logger.info("BGE-small-zh 模型加载完成")
        except ImportError:
            logger.warning("sentence-transformers 未安装，向量检索不可用")
            _model = False
        except Exception as e:
            logger.error(f"BGE 模型加载失败: {e}")
            _model = False
    return _model if _model is not False else None


def embed_texts(texts: list[str]) -> list[list[float]]:
    """将文本列表转为 512 维向量"""
    if not texts:
        return []
    model = get_model()
    if model is None:
        raise RuntimeError("Embedding 模型不可用")
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()
