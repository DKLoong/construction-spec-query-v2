"""术语/分类权威词典（termdict）——dim4/5/6 唯一合法标签源。

用法：写路径后调 invalidate_term_cache()；规则/AI label 判定用 is_valid_label()。
详见 docs/superpowers/specs/2026-09-05-termdict-design.md。
"""
from app.termdict.store import (  # noqa: F401
    DIMS, TermRow, word_conflict,
    is_valid_label, valid_labels, load_active_entries,
    upsert_term_label, invalidate_term_cache,
)
