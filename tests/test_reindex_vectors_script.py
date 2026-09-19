"""向量重建脚本的一致性守卫（源码级，不加载模型）

背景（2026-09-19）：`scripts/reindex_vectors.py` 曾内联重复实现 embedding 文本拼接，
绕过了 `build_embed_text`——后果有二：
1. 与导入路径格式漂移（空 title 时单空格 vs 双空格），同一批条文两条路径产出不同向量；
2. `build_embed_text` 接入 `plain_text` 后，重建会把 OCR 标记重新灌回向量。

本文件用源码断言守住「必须复用 build_embed_text」这一约束，避免再次漂移。
刻意不 import 脚本本体（其模块级 import 会拉起 embedding 模型）。
"""
from pathlib import Path

_REINDEX = Path(__file__).resolve().parents[1] / "scripts" / "reindex_vectors.py"


def test_reindex_vectors_reuses_build_embed_text():
    """重建脚本必须复用 build_embed_text，不得内联拼接文本"""
    src = _REINDEX.read_text(encoding="utf-8")
    assert "build_embed_text" in src, "必须调用 build_embed_text"
    assert "text_parts" not in src, "不得内联重复实现拼接（会绕过 plain_text 并再次漂移）"
