"""一次性探针：验证现有 strip_html 对真实表格 content 的清洗效果（throwaway）

Phase 3 最小假设检验：
  H1: strip_html 能从表格 content 里去掉 td/style/word/img/alt 等标记 token
  H2: strip_html 保留单元格文字（接头等级/Ⅰ级/...）
  H3: strip_html 不处理 LaTeX（预期为真，需补）
  H4: plain = strip_html + 丢 LaTeX 后，tokenize 结果里无标记词
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.text_clean import strip_html
from app.search.tokenize import tokenize

DB_PATH = "data/spec_query.db"
NOISE = {
    "td", "word", "style", "text", "align", "center", "wrap", "break", "tr",
    "colspan", "rowspan", "div", "table", "border", "margin", "auto", "img",
    "src", "alt", "image", "imgs", "jpg", "box", "in", "span", "font",
}
# LaTeX 定界：$...$ / \(...\) / \[...\]
LATEX = re.compile(r"\$[^$\n]{1,200}\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [
        (r["id"], r["clause_no"], r["content"] or "")
        for r in conn.execute("SELECT id, clause_no, content FROM clauses ORDER BY id")
    ]
    conn.close()

    affected = [(i, no, t) for i, no, t in rows if "<" in t or "$" in t]
    print(f"受影响条文: {len(affected)}")

    print("\n=== H1/H2: strip_html 效果（clause 439，最大表格）===")
    cid, no, raw = next((i, n, t) for i, n, t in affected if i == 439)
    stripped = strip_html(raw)
    print(f"  原文长度 {len(raw)}  ->  strip_html 后 {len(stripped)}")
    print(f"  去标签后前 160 字: {stripped[:160]!r}")

    def noise_tokens(text, tk_fn=tokenize):
        return [w for w in tk_fn(text) if w.lower() in NOISE]

    n_raw, n_str = len(noise_tokens(raw)), len(noise_tokens(stripped))
    print(f"  标记 token 数:  raw={n_raw}  ->  strip_html 后={n_str}")
    print(f"  H1 ({'PASS' if n_str == 0 else 'FAIL'})  H2 ", end="")
    print("PASS" if "接头类型" in stripped or "接头等级" in stripped else "FAIL")

    print("\n=== H3: strip_html 是否处理 LaTeX（预期 FAIL = 不处理）===")
    latex_left = LATEX.findall(stripped)
    print(f"  strip_html 后仍残留 LaTeX 段: {len(latex_left)}  例: {latex_left[:3]}")
    print(f"  H3 {'不处理 LaTeX（需补）' if latex_left else '意外已处理'}")

    print("\n=== H4: strip_html + 丢 LaTeX 的合并效果 ===")
    print("  " + "-" * 60)
    print(f"  {'clause':<10}{'原文长度':>9}{'清洗后':>8}{'标记token(raw)':>16}{'标记token(清洗)':>16}")
    bad_total = 0
    for i, no, t in affected:
        s = LATEX.sub(" ", strip_html(t))
        nr, ns = len(noise_tokens(t)), len(noise_tokens(s))
        bad_total += ns
        flag = "" if ns == 0 else "  <-- 仍有残留!"
        print(f"  {str(i):<10}{len(t):>9}{len(s):>8}{nr:>16}{ns:>16}{flag}")
    print("  " + "-" * 60)
    print(f"  清洗后标记 token 合计: {bad_total}")

    print("\n=== 幂等性 + 不误伤工程写法 ===")
    samples = ["HRB400 钢筋", "C30 混凝土", "第 5.1.1 条", "接头等级 Ⅰ级", "N/mm2"]
    for s in samples:
        out = LATEX.sub(" ", strip_html(s)).strip()
        ok = "OK" if out == s else f"变了 -> {out!r}"
        print(f"  {s!r:<24} {ok}")
    plain = strip_html("接头等级 Ⅰ级")
    twice = LATEX.sub(" ", strip_html(plain)).strip()
    print(f"  幂等: {'OK' if twice == plain else f'FAIL -> {twice!r}'}")

    print("\n=== 空/纯标记 content 的边界 ===")
    for s in ["", "<div></div>", "<table><tr><td></td></tr></table>", "<img src='a.jpg' alt='Image' />"]:
        out = LATEX.sub(" ", strip_html(s)).strip()
        print(f"  {s!r:<46} -> {out!r}")


if __name__ == "__main__":
    rc = getattr(sys.stdout, "reconfigure", None)
    if callable(rc):
        rc(encoding="utf-8")
    main()
