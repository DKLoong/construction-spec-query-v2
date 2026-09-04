"""易混淆命中检测（纯函数）。

语义校准（spec §5.4）：针对「原问句同现两个易混淆概念、用户需要区分」的场景
（如「钢筋和箍筋有何不同」）。不做「单用歧义」推断——用户只问 A 时不提示可能
意指 B。行级校验已拒绝互含子串词对，避免长词语境恒命中。
"""
from app.lexicon.store import LexiconRow


def detect_confusable(text: str, pairs: list[LexiconRow]) -> list[dict]:
    if not text:
        return []
    hits = []
    for p in pairs:
        if p.canonical not in text:
            continue
        for v in p.variants:
            if v and v in text:
                hits.append({"a": p.canonical, "b": v, "distinguish": p.distinguish})
                break
    return hits
