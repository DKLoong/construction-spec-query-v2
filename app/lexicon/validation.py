"""词库行级校验（路由表单与 CSV 导入共用，单一来源）。"""
from app.lexicon.store import KIND_ALIAS, KIND_CONFUSABLE, KIND_SYNONYM

_KINDS = (KIND_SYNONYM, KIND_ALIAS, KIND_CONFUSABLE)
_EQUIV = (KIND_SYNONYM, KIND_ALIAS)


def _split(variants: str) -> list[str]:
    return [v.strip() for v in (variants or "").split(",") if v.strip()]


def validate_row(kind: str, canonical: str, variants: str, distinguish: str = "") -> tuple[dict | None, str | None]:
    """校验并返回 (净数据 dict | None, 错误信息 | None)。"""
    kind = (kind or "").strip()
    canonical = (canonical or "").strip()
    if kind not in _KINDS:
        return None, "kind 不合法"
    if not canonical:
        return None, "代表词/词 A 不能为空"
    vs = _split(variants)
    if kind == KIND_CONFUSABLE:
        if len(vs) != 1:
            return None, "易混淆须为 词A + 词B（variants 恰一个词）"
        if not distinguish or not distinguish.strip():
            return None, "易混淆必须填写区分说明"
        if vs[0] == canonical:
            return None, "词A 与 词B 不能相同"
        if vs[0] in canonical or canonical in vs[0]:
            return None, "词A 与 词B 不得互为子串（防命中自误报）"
    else:
        if not vs:
            return None, "变体/俗称词不能为空"
        for v in vs:
            if v == canonical:
                return None, f"变体词「{v}」不能与代表词相同"
            if v in canonical or canonical in v:
                return None, f"变体词「{v}」不得与代表词互为子串"
    return {"kind": kind, "canonical": canonical, "variants": ",".join(vs),
            "distinguish": distinguish.strip()}, None
