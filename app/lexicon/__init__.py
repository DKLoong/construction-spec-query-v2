"""词库子系统：三类词条（synonym/alias/confusable）的数据加载与纯逻辑消费。

边界（写注释）：同义词/别名只做 FTS 检索扩展与条文归一化，向量/embedding 输入
不替换原词；confusable 只做命中告警，绝不用于检索改写。
"""
from app.lexicon.store import (  # noqa: F401
    KIND_ALIAS, KIND_CONFUSABLE, KIND_SYNONYM, EQUIV_KINDS,
    LexiconRow, invalidate_lexicon_caches, load_confusable_pairs,
    load_equivalent_groups,
)
