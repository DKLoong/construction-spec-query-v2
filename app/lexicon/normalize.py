"""条文归一化：把 active 等价组中的变体词替换为组 canonical（仅 synonym/alias）。

替换方向唯一：variants → canonical，绝不反向替换 canonical。变体按词长降序处理，
避免短俗词抢先替换破坏长词（沿用 rule_engine 旧语义）。局限（注释）：单字俗词
（如「砼」）在极端语境可能误伤词内同形字，工程词库语境可接受。
"""
from app.lexicon.store import EQUIV_KINDS, LexiconRow


def normalize_text(text: str, groups: list[LexiconRow]) -> str:
    if not text or not groups:
        return text
    repl = []
    for g in groups:
        if g.kind not in EQUIV_KINDS:
            continue
        for v in g.variants:
            if v and v != g.canonical:
                repl.append((v, g.canonical))
    repl.sort(key=lambda x: len(x[0]), reverse=True)
    out = text
    for v, c in repl:
        out = out.replace(v, c)
    return out
