"""LanceDB 建表 schema 必须只有一个定义处。

历史：schema 在三处逐字重复（vector_search.index_clause / batch_index /
import_routes），加列时漏改一处会产生「表按新 schema 建、写入按旧 schema 走」
的静默错配。本测试用源码断言守住「只有一处 pa.schema([...]) 定义」。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_only_one_embedding_schema_definition():
    src = (ROOT / "app/search/vector_search.py").read_text(encoding="utf-8")
    assert src.count("pa.schema([") == 1, "schema 定义应只在 embedding_schema 工厂里出现一次"

    routes = (ROOT / "app/routes/import_routes.py").read_text(encoding="utf-8")
    assert "pa.schema([" not in routes, "import_routes 不得自行定义 schema"


def test_factory_produces_expected_fields():
    from app.search.vector_search import embedding_schema
    s = embedding_schema(8)
    assert [f.name for f in s] == [
        "clause_id", "spec_id", "text", "embedding", "dim_scores", "chunk_index",
    ]
    assert s.field("embedding").type.list_size == 8
