"""检索查询扩展：词条感知切词 + OR 组，生成 FTS5 MATCH 串。

只改查询端——索引 search_text 与向量输入均不改一字（spec 边界 1）。
groups 为空/开关关时直接委托 app.search.tokenize.build_match_query，保证旧行为
与既有检索测试逐字节一致（退化护栏）。
"""
from app.lexicon.store import LexiconRow


def _quote(word: str) -> str:
    return '"' + word.replace('"', '""') + '"'


def _terms_by_len(groups: list[LexiconRow]) -> list[tuple[int, str]]:
    """返回 (组下标, 词) 按词长降序；同一词跨组已被 store 校验拒绝，term→组唯一。"""
    items: dict[str, int] = {}
    for gi, g in enumerate(groups):
        for w in [g.canonical, *g.variants]:
            items.setdefault(w, gi)
    return [(gi, w) for w, gi in sorted(items.items(), key=lambda kv: len(kv[0]), reverse=True)]


def _items(keyword: str, groups: list[LexiconRow]) -> list[tuple[str, object]]:
    """把 keyword 切成有序项列表。

    返回 list of ('group', (gi, hit_word)) | ('seg', 子串)；seg 交给 jieba 再切。
    """
    terms = _terms_by_len(groups)
    pieces: list[tuple[str, object]] = []
    i, n = 0, len(keyword)
    while i < n:
        matched = False
        for gi, w in terms:
            if keyword.startswith(w, i):
                pieces.append(("group", (gi, w)))
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
            pieces.append(("seg", keyword[i:j]))
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
    for kind, payload in _items(keyword, groups):
        if kind == "group":
            gi, _ = payload
            words = [_quote(w) for w in _group_words(groups, gi)]
            if len(words) == 1:
                tokens.append(words[0])
            else:
                tokens.append("(" + " OR ".join(words) + ")")
        else:
            from app.search.tokenize import tokenize
            for tok in tokenize(payload):
                tokens.append(_quote(tok))
    if not tokens:
        return ""
    return f" {join_with} ".join(tokens)
