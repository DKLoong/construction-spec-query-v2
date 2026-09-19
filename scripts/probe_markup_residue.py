"""一次性探针：穷举 clauses.content 里的标记构造（throwaway）

用途：为「纯文本提取器」确定必须覆盖的标记形态 → 直接决定测试用例。
不写库、不改业务代码。
"""

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = "data/spec_query.db"

HTML_TAG = re.compile(r"<[a-zA-Z/!][^>]{0,80}>")
LATEX = re.compile(r"\$[^$]{1,60}\$")
IMG = re.compile(r"<img[^>]*>")
LATEX_ALT = re.compile(r"\\\(.*?\\\)|\\\[.*?\\\]")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [
        (r["id"], r["clause_no"], r["content"] or "")
        for r in conn.execute(
            "SELECT id, clause_no, content FROM clauses ORDER BY id"
        )
    ]
    conn.close()

    affected = [(i, no, t) for i, no, t in rows if HTML_TAG.search(t) or LATEX.search(t)]
    print(f"条文总数: {len(rows)}    含标记的条文: {len(affected)}")

    print("\n=== 必须覆盖的标记构造 ===")
    counts = Counter()
    for _, _, t in affected:
        for m in HTML_TAG.findall(t):
            counts["HTML: " + re.sub(r"\s+", " ", m)[:52]] += 1
        for m in LATEX.findall(t):
            counts["LaTeX: " + m[:50]] += 1
    for k, v in counts.most_common(30):
        print(f"  {v:>5}x  {k}")

    print("\n=== 表格：行/单元格规模 ===")
    for cid, no, t in affected:
        tr = t.count("<tr")
        td = t.count("<td")
        if tr:
            print(f"  clause {cid} ({no}): <tr> {tr}, <td> {td}")

    print("\n=== 正文与表格混排（提取器要保留正文+表格文字、丢标签）===")
    mixed = 0
    for _, _, t in affected:
        head = t.split("<table")[0]
        head = HTML_TAG.sub("", head).strip()
        if len(head) > 10:
            mixed += 1
    print(f"  表格前有实质正文的条文: {mixed} / {len(affected)}")

    print("\n=== LaTeX 形态（行内/显示式/反斜杠定界）===")
    forms = Counter()
    for _, _, t in affected:
        if LATEX.search(t):
            forms["$...$ 行内"] += 1
        if LATEX_ALT.search(t):
            forms["\\(...\\) 或 \\[...\\]"] += 1
    for k, v in forms.most_common():
        print(f"  {k}: {v} 条")

    print("\n=== 图片构造 ===")
    for _, _, t in affected:
        for m in IMG.findall(t)[:1]:
            print(f"  {m[:100]}")
            break

    print("\n=== 提取后应当消失的 token（拿 affected 条文实测）===")
    import jieba

    jieba.setLogLevel(60)
    from app.search.tokenize import tokenize

    bad = Counter()
    for _, _, t in affected:
        for w in tokenize(t):
            lw = w.lower()
            if (w.isascii() and w.isalpha() and len(w) >= 2) or w in {"，", "。"}:
                bad[lw] += 1
    print("  当前被切成 token 的 ascii 词（前 30）:")
    print("   ", dict(bad.most_common(30)))


if __name__ == "__main__":
    rc = getattr(sys.stdout, "reconfigure", None)
    if callable(rc):
        rc(encoding="utf-8")
    main()
