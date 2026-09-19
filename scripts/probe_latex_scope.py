"""一次性探针：核实 LaTeX 命令是否全部落在 $...$ 定界符内（throwaway）

决定 'plain_text' 的 LaTeX 正则是否够用：
  - 若 mathrm/times/alpha 等命令全在 $...$ 内 -> 丢 $...$ 即可
  - 若定界符外有裸命令 -> 需额外处理
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = "data/spec_query.db"
LATEX = re.compile(r"\$\$.*?\$\$|\$[^$\n]{1,200}\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)
BARE_CMD = re.compile(r"\\(?:mathrm|times|alpha|beta|epsilon|ge|le|cdot|frac|sqrt|pm|leq|geq)\b")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [(r["id"], r["content"] or "") for r in conn.execute(
        "SELECT id, content FROM clauses ORDER BY id")]
    conn.close()

    print("=== $ 配对性 ===")
    odd = 0
    for cid, t in rows:
        n = t.count("$")
        if n and n % 2:
            print(f"  clause {cid}: $ x{n}  <-- 奇数，不成对!")
            odd += 1
    print(f"  含 $ 的条文共 {sum(1 for _, t in rows if '$' in t)} 条，奇数(不成对) {odd} 条")

    print("\n=== 丢弃 $...$ 后是否还有裸 LaTeX 命令残留 ===")
    leftover = 0
    for cid, t in rows:
        stripped = LATEX.sub(" ", t)
        for m in BARE_CMD.finditer(stripped):
            s = max(0, m.start() - 50)
            print(f"  clause {cid}: ...{stripped[s:m.end() + 50]!r}...")
            leftover += 1
    print(f"  定界符外的裸 LaTeX 命令: {leftover}")

    print("\n=== 全部 LaTeX 段样本（去重后）===")
    seen = {}
    for cid, t in rows:
        for m in LATEX.findall(t):
            seen.setdefault(m[:60], 0)
            seen[m[:60]] += 1
    for k, v in sorted(seen.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {v:>3}x  {k}")


if __name__ == "__main__":
    rc = getattr(sys.stdout, "reconfigure", None)
    if callable(rc):
        rc(encoding="utf-8")
    main()
