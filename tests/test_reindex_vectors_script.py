"""向量重建脚本的一致性守卫（源码级，不加载模型）

背景（2026-09-19）：`scripts/reindex_vectors.py` 曾内联重复实现 embedding 文本拼接，
绕过了 `build_embed_text`——后果有二：
1. 与导入路径格式漂移（空 title 时单空格 vs 双空格），同一批条文两条路径产出不同向量；
2. `build_embed_text` 接入 `plain_text` 后，重建会把 OCR 标记重新灌回向量。

2026-09-28（批二 U5）起，四条写入路径共用的入口上移为
`app.search.chunking.build_embed_chunks`（内部才调 `build_embed_text`，并统一做
超长条文切块与前缀预算预留）。故这里断言的是**共用入口**——若写回
`build_embed_text`，重建就绕过了切块（长条文尾部永久不可召回）与前缀预留。

本文件用源码断言守住该约束，避免再次漂移。
刻意不 import 脚本本体（其模块级 import 会拉起 embedding 模型）。
"""
from pathlib import Path

_REINDEX = Path(__file__).resolve().parents[1] / "scripts" / "reindex_vectors.py"


def test_reindex_vectors_reuses_shared_embed_chunk_builder():
    """重建脚本必须复用 build_embed_chunks，不得内联拼接文本、也不得跳过切块"""
    src = _REINDEX.read_text(encoding="utf-8")
    assert "build_embed_chunks" in src, "必须调用 build_embed_chunks（切块 + 前缀预留的唯一入口）"
    assert "text_parts" not in src, "不得内联重复实现拼接（会绕过 plain_text 并再次漂移）"
