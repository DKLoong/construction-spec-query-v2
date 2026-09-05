"""术语/分类权威词典数据访问：DB 读取 + TTL 缓存 + 词面唯一一致性校验 + 写 helper。

缓存仿 app/lexicon/store.py（TTL 60s + DATABASE_PATH 守卫，测试换库不串）。
词典「不可用」语义统一于此：加载失败或词面冲突 → is_valid_label 恒 True（放行）、
valid_labels 恒 []（候选兜底现值）——四处闸门不各自判断，导入不因词典故障被阻断。
写路径（routes/人工确认/迁移）变更后必须调 invalidate_term_cache()（唯一失效出口）。
"""
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DIMS = ("dim4", "dim5", "dim6")   # 词典收口维度域；dim2/3/1 不在域内恒放行

_TTL = 60.0


@dataclass
class TermRow:
    id: int
    dimension: str
    label: str
    canonical: str
    aliases: list
    source: str
    note: str = ""
    is_active: int = 1


_cache: list[TermRow] | None = None
_cache_ts = 0.0
_cache_path: str | None = None
# 词面唯一冲突：(dimension, word) —— 同维词面指向多个 label；None 表示无冲突
word_conflict: tuple[str, str] | None = None
# 词典是否可信（False=加载失败或词面冲突，is_valid/valid_* 进入放行语义）
_trusted = True


def _row_from(rec: dict) -> TermRow:
    aliases = [v.strip() for v in (rec.get("aliases") or "").split(",") if v.strip()]
    return TermRow(
        id=rec["id"], dimension=rec["dimension"], label=rec["label"],
        canonical=rec["canonical"], aliases=aliases,
        source=rec.get("source") or "manual", note=rec.get("note") or "",
        is_active=rec.get("is_active", 1),
    )


def _fresh() -> list[TermRow]:
    """从 DB 加载全部 active 词条（无缓存污染）。"""
    from app.database import get_db
    rows = []
    with get_db() as conn:
        for rec in conn.execute(
            "SELECT id, dimension, label, canonical, aliases, source, note, is_active "
            "FROM term_labels WHERE is_active = 1 ORDER BY id"
        ).fetchall():
            rows.append(_row_from(dict(rec)))
    return rows


def _check_word_unique(rows: list[TermRow]) -> bool:
    """同一维度内一个词面(canonical∪aliases)只能指向一个 label。

    通过时 word_conflict 置 None；冲突时记录 (dimension,word) 并返回 False。
    """
    global word_conflict
    word_conflict = None
    owner: dict[tuple[str, str], str] = {}   # (dimension, word) -> label
    for r in rows:
        for w in [r.canonical, *r.aliases]:
            key = (r.dimension, w)
            prev = owner.get(key)
            if prev is not None and prev != r.label:
                word_conflict = (r.dimension, w)
                return False
            owner[key] = r.label
    return True


def _load_all() -> list[TermRow]:
    """加载词典（active）。不可用(失败/冲突)时返回空表并置 _trusted=False。"""
    global _cache, _cache_ts, _cache_path, _trusted
    from app.database import DATABASE_PATH
    now = time.monotonic()
    if _cache is not None and _cache_path == DATABASE_PATH and (now - _cache_ts) < _TTL:
        return _cache
    try:
        if not os.path.exists(DATABASE_PATH):
            rows: list[TermRow] = []
        else:
            rows = _fresh()
        if _check_word_unique(rows):
            _cache = rows
            _trusted = True
        else:
            logger.warning("词典词面冲突（同维词面指向多 label），本次加载作废: %s",
                           word_conflict)
            _cache = []
            _trusted = False
    except Exception as e:
        logger.warning("词典加载失败，回退不可用(放行)语义: %s", e)
        _cache = []
        _trusted = False
    _cache_ts = now
    _cache_path = DATABASE_PATH
    return _cache


def _is_trusted(rows: list[TermRow]) -> bool:
    """加载结果是否可信。调用前先 _load_all() 填 _trusted。"""
    return _trusted


def valid_labels(dimension: str) -> list[str]:
    """active 权威 label 清单（白名单；AI prompt 候选主源）。不可用 → []。"""
    if dimension not in DIMS:
        return []
    rows = _load_all()
    if not rows or not _trusted:
        return []
    return [r.label for r in rows if r.dimension == dimension]


def is_valid_label(dimension: str, label: str) -> bool:
    """label 是否为该维度 active 权威标签。

    - dim 不在 DIMS → True（不拦截 dim2/3/1）
    - 词典不可用（失败/冲突）→ True（放行，不阻断分类）
    - 词典为空（收口前过渡）→ True
    - 词典正常但 label 不在白名单 → False（→ 转 review）
    """
    if dimension not in DIMS:
        return True
    lbl = (label or "").strip()
    if not lbl:
        return False
    rows = _load_all()
    if not rows or not _trusted:
        return True
    return any(r.dimension == dimension and r.label == lbl for r in rows)


def load_active_entries(dimension: str | None = None) -> list[TermRow]:
    rows = _load_all()
    if not rows or not _trusted:
        return []
    if dimension is not None:
        return [r for r in rows if r.dimension == dimension]
    return list(rows)


def upsert_term_label(conn, dimension: str, label: str, canonical: str = "",
                      source: str = "manual", note: str = "",
                      aliases: str = "") -> int:
    """确保 (dimension,label) 权威行存在；已存在则把提供词面并入 aliases（去重）。

    供路由新建 / process_feedback 人工确认 / 迁移共用。调用方须持写连接，
    且本行词面子串自检由 validation.validate_row 在外部先行通过。
    返回行 id。
    """
    label = (label or "").strip()
    canonical = (canonical or "").strip()
    row = conn.execute(
        "SELECT id, canonical, aliases FROM term_labels WHERE dimension = ? AND label = ?",
        (dimension, label)).fetchone()
    if row:
        rid = row["id"]
        existing = [a.strip() for a in (row["aliases"] or "").split(",") if a.strip()]
        old_canon = (row["canonical"] or "").strip()
        merge = []
        for w in [canonical, *[a.strip() for a in aliases.split(",") if a.strip()]]:
            if w and w != old_canon and w not in existing and w not in merge:
                merge.append(w)
        if merge:
            conn.execute(
                "UPDATE term_labels SET aliases = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (",".join(existing + merge), rid))
        return rid
    cur = conn.execute(
        "INSERT INTO term_labels (dimension, label, canonical, aliases, source, note)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (dimension, label, canonical,
         ",".join(a.strip() for a in aliases.split(",")
                  if a.strip() and a.strip() != canonical),
         source, note))
    return cur.lastrowid


def word_conflict_owner(conn, dimension: str, label: str,
                        words: list[str]) -> tuple[str, str] | None:
    """任一词已属于同维其它 active label → 返回 (word, owner_label)；否则 None。

    与 _check_word_unique 同口径（active 空间、label != self），供人工确认写路径与
    路由新建/编辑共用：词面归属冲突须在 upsert 前拦截，避免制造同维词面双归属而
    翻转词典「放行」模式、使收口静默失效。
    """
    own: dict[str, str] = {}
    rows = conn.execute(
        "SELECT label, canonical, aliases FROM term_labels "
        "WHERE dimension = ? AND is_active = 1 AND label != ?",
        (dimension, label)).fetchall()
    for r in rows:
        for w in [r["canonical"],
                  *[a for a in (r["aliases"] or "").split(",") if a.strip()]]:
            own.setdefault(w, r["label"])
    for w in words:
        if w in own:
            return (w, own[w])
    return None


def invalidate_term_cache() -> None:
    """清词典缓存（唯一失效出口）。词典与检索/词库缓存解耦，不联动清它们。"""
    global _cache, _cache_ts, _cache_path, _trusted, word_conflict
    _cache = None
    _cache_ts = 0.0
    _cache_path = None
    _trusted = True
    word_conflict = None
