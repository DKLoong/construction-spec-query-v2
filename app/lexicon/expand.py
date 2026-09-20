"""检索查询扩展：词条感知切词 + OR 组，生成 FTS5 MATCH 串。

只改查询端——索引 search_text 与向量输入均不改一字（spec 边界 1）。
groups 为空/开关关时直接委托 app.search.tokenize.build_match_query，保证旧行为
与既有检索测试逐字节一致（退化护栏）。
"""
from typing import NamedTuple

from app.lexicon.store import LexiconRow


class _GroupItem(NamedTuple):
    """命中某个等价组 → 整组 OR 扩展；gi 为 groups 下标。"""
    gi: int


class _SegItem(NamedTuple):
    """未命中任何组 → 交给 jieba 再切。"""
    text: str


# 用两个 NamedTuple 的联合 + isinstance 分派，而非 `(kind, payload)` 元组：
# **Pyright/静态检查不做元组跨元素的相关窄化**（`if kind == "group"` 不会窄化
# payload，实测 confirm），故元组形态必然留下"payload 是 object/联合"的报错。
# isinstance 的类窄化则完整支持。见 tests/test_lexicon_expand.py 覆盖。
_Item = _GroupItem | _SegItem


def _quote(word: str) -> str:
    return '"' + word.replace('"', '""') + '"'


def _terms_by_len(groups: list[LexiconRow]) -> list[tuple[int, str]]:
    """返回 (组下标, 词) 按词长降序；同一词跨组已被 store 校验拒绝，term→组唯一。"""
    items: dict[str, int] = {}
    for gi, g in enumerate(groups):
        for w in [g.canonical, *g.variants]:
            if w:  # 空词不入表：startswith("") 恒真会让 _items 死循环（store 对 canonical 无空值守卫）
                items.setdefault(w, gi)
    return [(gi, w) for w, gi in sorted(items.items(), key=lambda kv: len(kv[0]), reverse=True)]


def _items(keyword: str, groups: list[LexiconRow]) -> list[_Item]:
    """把 keyword 切成有序项列表：命中组 → `_GroupItem`，未命中 → `_SegItem`。"""
    terms = _terms_by_len(groups)
    pieces: list[_Item] = []
    i, n = 0, len(keyword)
    while i < n:
        matched = False
        for gi, w in terms:
            if keyword.startswith(w, i):
                pieces.append(_GroupItem(gi))
                i += len(w)
                matched = True
                break
        if matched:
            continue
        j = i
        while j < n:
            if any(keyword.startswith(w, j) for _, w in terms):
                break
            j += 1
        if j > i:
            pieces.append(_SegItem(keyword[i:j]))
        i = j
    return pieces


def _group_words(groups: list[LexiconRow], gi: int) -> list[str]:
    seen: list[str] = []
    for w in [groups[gi].canonical, *groups[gi].variants]:
        if w and w not in seen:
            seen.append(w)
    return seen


def build_expanded_match(keyword: str, groups: list[LexiconRow],
                         join_with: str = "AND") -> str:
    """生成 FTS5 MATCH 串；组内候选词 OR、项间按 join_with 连接；空词返回空串。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return ""
    if not groups:  # 退化委托：开关关或无词条
        from app.search.tokenize import build_match_query
        return build_match_query(keyword, join_with)

    tokens: list[str] = []
    for item in _items(keyword, groups):
        if isinstance(item, _GroupItem):
            words = [_quote(w) for w in _group_words(groups, item.gi)]
            if len(words) == 1:
                tokens.append(words[0])
            else:
                tokens.append("(" + " OR ".join(words) + ")")
        else:
            from app.search.tokenize import tokenize
            for tok in tokenize(item.text):
                tokens.append(_quote(tok))
    if not tokens:
        return ""
    return f" {join_with} ".join(tokens)
