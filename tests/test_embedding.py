def test_embedding_module_imports():
    from app.ai.embedding import embed_texts, get_model
    assert callable(embed_texts)
    assert callable(get_model)

def test_get_model_returns_same_instance():
    from app.ai.embedding import get_model
    m1 = get_model()
    m2 = get_model()
    assert m1 is m2  # 单例

def test_embed_texts_returns_correct_shape():
    """如 BGE 未安装或模型未下载则跳过"""
    import pytest
    sentence_transformers = pytest.importorskip("sentence_transformers")
    from app.ai.embedding import embed_texts, get_model
    # 如果模型加载失败（如首次下载超时），跳过测试
    model = get_model()
    if model is None:
        pytest.skip("BGE 模型未就绪（可能正在下载或网络不可用）")
    texts = ["混凝土结构施工", "钢筋绑扎要求"]
    vectors = embed_texts(texts)
    assert len(vectors) == 2
    assert len(vectors[0]) == 512  # BGE-small-zh 输出 512 维
    assert isinstance(vectors[0][0], float)

def test_embed_texts_empty():
    from app.ai.embedding import embed_texts
    assert embed_texts([]) == []
