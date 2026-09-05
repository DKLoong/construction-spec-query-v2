"""词典行级校验（路由 create/edit 与迁移共用，单一来源）。

跨行「同维词面→恰一 label」一致性由 store._check_word_unique 在加载期保证（冲突禁载）；
本文件只做本行内校验 + 拆词。
"""
from app.termdict.store import DIMS


def split_aliases(aliases: str) -> list[str]:
    return [a.strip() for a in (aliases or "").split(",") if a.strip()]


def validate_row(dimension: str, label: str, canonical: str, aliases: str = "",
                 source: str = "manual", note: str = "") -> tuple[dict | None, str | None]:
    dimension = (dimension or "").strip()
    label = (label or "").strip()
    canonical = (canonical or "").strip()
    source = (source or "").strip() or "manual"
    if dimension not in DIMS:
        return None, "维度不合法（词典仅覆盖 dim4/5/6）"
    if not label:
        return None, "权威标签不能为空"
    if not canonical:
        return None, "代表词不能为空"
    als = []
    for a in split_aliases(aliases):
        if a in als:
            continue
        if a == canonical:
            continue                      # canonical 不入 aliases
        if a in canonical or canonical in a:
            return None, f"词面「{a}」不得与代表词「{canonical}」互为子串"
        als.append(a)
    return {
        "dimension": dimension, "label": label, "canonical": canonical,
        "aliases": ",".join(als), "source": source, "note": (note or "").strip(),
    }, None
