"""词库数据访问：DB 读取 + 模块级 TTL 缓存 + 词表一致性校验。

缓存仿 rule_engine 旧同义词缓存（TTL 60s + DATABASE_PATH 守卫，测试换库不串）。
词库写路径（routes/CSV）变更后必须调 invalidate_lexicon_caches()（唯一失效出口，
同时延迟调用 hybrid_search.clear_search_cache()）。
"""
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

KIND_SYNONYM = "synonym"
KIND_ALIAS = "alias"
KIND_CONFUSABLE = "confusable"
EQUIV_KINDS = (KIND_SYNONYM, KIND_ALIAS)

_TTL = 60.0


@dataclass
class LexiconRow:
    """一行 lexicon_entries 的词条行（variants 已拆为列表）。"""
    id: int
    kind: str
    canonical: str
    variants: list[str]
    distinguish: str = ""
    is_active: int = 1


_cache: list[LexiconRow] | None = None
_cache_ts = 0.0
_cache_path: str | None = None
# 词表一致性冲突词（同一词属多个 equiv 组）；None 表示无冲突
equiv_conflict_word: str | None = None


def _row_from(rec: dict) -> LexiconRow:
    variants = [v.strip() for v in (rec.get("variants") or "").split(",") if v.strip()]
    return LexiconRow(
        id=rec["id"], kind=rec["kind"], canonical=rec["canonical"],
        variants=variants, distinguish=rec.get("distinguish") or "",
        is_active=rec.get("is_active", 1),
    )


def _fresh() -> list[LexiconRow]:
    """从 DB 加载全部 active 词条（无缓存污染）；失败/无冲突校验用逻辑见 _load_all。"""
    from app.database import get_db
    rows = []
    with get_db() as conn:
        for rec in conn.execute(
            "SELECT id, kind, canonical, variants, distinguish, is_active "
            "FROM lexicon_entries WHERE is_active = 1 ORDER BY id"
        ).fetchall():
            rows.append(_row_from(dict(rec)))
    return rows


def _check_equiv_unique(rows: list[LexiconRow]) -> bool:
    """同一词不得同时属于多个 equiv 组（canonical/variants 合计）。confusable 独立。

    通过时 equiv_conflict_word 置 None；冲突时记录冲突词并返回 False。
    """
    global equiv_conflict_word
    equiv_conflict_word = None
    owner: dict[str, int] = {}
    for r in rows:
        if r.kind not in EQUIV_KINDS:
            continue
        for w in [r.canonical, *r.variants]:
            prev = owner.get(w)
            if prev is not None and prev != r.id:
                equiv_conflict_word = w
                return False
            owner[w] = r.id
    return True


def _load_all() -> list[LexiconRow]:
    global _cache, _cache_ts, _cache_path
    from app.database import DATABASE_PATH
    now = time.monotonic()
    if _cache is not None and _cache_path == DATABASE_PATH and (now - _cache_ts) < _TTL:
        return _cache
    try:
        if not os.path.exists(DATABASE_PATH):
            rows: list[LexiconRow] = []
        else:
            rows = _fresh()
        if _check_equiv_unique(rows):
            _cache = rows
        else:
            logger.warning("词库 equiv 词条冲突（同一词属多组），本次加载作废: %s",
                           equiv_conflict_word)
            _cache = []
    except Exception as e:
        logger.warning("词库加载失败，回退空列表: %s", e)
        _cache = []
    _cache_ts = now
    _cache_path = DATABASE_PATH
    return _cache


def load_equivalent_groups() -> list[LexiconRow]:
    """active 的 synonym/alias 组（供检索扩展与规则归一化）。冲突时返回空列表。

    追加 is_active==1 防御过滤：DB 侧已只取 active，此处保证注入/未来缓存
    含停用行时也不外泄（词表一致性）。
    """
    return [r for r in _load_all() if r.kind in EQUIV_KINDS and r.is_active == 1]


def load_confusable_pairs() -> list[LexiconRow]:
    """active 的 confusable 组（供命中检测）。"""
    return [r for r in _load_all() if r.kind == KIND_CONFUSABLE and r.is_active == 1]


def invalidate_lexicon_caches() -> None:
    """清词库缓存 + 检索结果缓存。词库任何写操作后必须调用。"""
    global _cache, _cache_ts, _cache_path, equiv_conflict_word
    _cache = None
    _cache_ts = 0.0
    _cache_path = None
    equiv_conflict_word = None
    try:
        from app.search.hybrid_search import clear_search_cache  # 延迟：避免包级环依赖
        clear_search_cache()
    except Exception as e:
        logger.debug("clear_search_cache 调用失败: %s", e)
