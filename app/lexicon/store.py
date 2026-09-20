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


def _split_conflicts(rows: list[LexiconRow]) -> tuple[list[LexiconRow], list[int], list[str]]:
    """按「一个词只能属于一个等价组」把行集拆成 (保留行, 被剔除的行 id, 冲突词)。

    只有 synonym/alias 参与词面占用（与写入侧 `find_equiv_conflict` 同口径）；
    confusable 是独立命名空间，永不冲突、永不被剔除。

    **降级策略（2026-09-20 改）**：冲突簇内的行**全部**剔除，而不是丢弃整张表。
    此前「发现任一冲突即整表作废」曾让一次数据瑕疵（76 个冲突词）造成 509 条等价组
    + 211 条 confusable 全部失效 13 天。剔除是**对称**的（不依赖行序，不搞「保第一行」
    那种任意取舍）；被剔除的组不可信，但其余组照常可用。
    """
    global equiv_conflict_word
    owner: dict[str, int] = {}
    conflicting: set[int] = set()
    words: list[str] = []
    for r in rows:
        if r.kind not in EQUIV_KINDS:
            continue
        for w in [r.canonical, *r.variants]:
            prev = owner.get(w)
            if prev is not None and prev != r.id:
                conflicting.add(prev)
                conflicting.add(r.id)
                words.append(w)
            else:
                owner[w] = r.id
    equiv_conflict_word = words[0] if words else None
    return ([r for r in rows if r.id not in conflicting],
            sorted(conflicting), words)


def find_equiv_conflict(conn, words, exclude_id: int | None = None) -> str | None:
    """写入侧校验：这些词面是否已属于**别的** equiv 组？返回首个冲突词或 None。

    **必须与读侧 `_split_conflicts` 同样严格**——两边口径不一致（写入侧更宽松）时，
    就能写进读侧会拒绝的数据，即「一次 CSV 导入搞死整个词库」（2026-09-19 事故）。
    故对齐三点：canonical 与 variants 全算、跨 kind（alias/synonym 同一命名空间）、
    精确匹配。

    - `exclude_id`：编辑/合并时排除自身行（自己占自己的词面不算冲突）
    - confusable 是独立命名空间，不参与 equiv 词面占用

    词条量级很小（<1000 行），故整表读出在内存比对，避免 variants 逗号串的
    SQL 精确匹配难题。
    """
    wanted = {w.strip() for w in (words or []) if w and w.strip()}
    if not wanted:
        return None
    rows = conn.execute(
        "SELECT id, canonical, variants FROM lexicon_entries WHERE kind IN (?, ?)",
        EQUIV_KINDS,
    ).fetchall()
    for r in rows:
        if exclude_id is not None and r["id"] == exclude_id:
            continue
        owned = {r["canonical"]}
        owned |= {v.strip() for v in (r["variants"] or "").split(",") if v.strip()}
        hit = wanted & owned
        if hit:
            return sorted(hit)[0]
    return None


def _load_all() -> list[LexiconRow]:
    global _cache, _cache_ts, _cache_path
    from app.database import DATABASE_PATH
    now = time.monotonic()
    if _cache is not None and _cache_path == DATABASE_PATH and (now - _cache_ts) < _TTL:
        return _cache
    try:
        rows: list[LexiconRow] = _fresh() if os.path.exists(DATABASE_PATH) else []
        kept, dropped, words = _split_conflicts(rows)
        if dropped:
            # ERROR 级：这是「部分功能失效」，必须在日志管理界面可见（此前 WARNING
            # 且不落库，导致 13 天无人发现）。detail 里带冲突词与剔除规模。
            logger.error("词库同词跨组冲突：剔除 %d 组、保留 %d 组（冲突词：%s）",
                         len(dropped), len(kept),
                         "、".join(dict.fromkeys(words))[:120])
        _cache = kept
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
