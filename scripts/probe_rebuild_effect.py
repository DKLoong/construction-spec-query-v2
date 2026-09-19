"""一次性探针：标记残留修复前后的检索效果对照（throwaway）

对比对象：
  - 修复前：data/backups/spec_query.db.before_markup_fix_* 里的 search_text
  - 修复后：data/spec_query.db 当前的 search_text
方法：把两份 search_text 分别灌进内存 FTS5，跑同一组查询比对命中。

验证目标：
  1. 标记词查询（style/td/word/mathrm）应归零 —— 噪声查询不再命中规范
  2. 正文/表格文字查询（接头极限抗拉强度、残余变形…）命中应保持或提升
  3. embedding 文本长度应大幅收缩（向量不再由标记主导）
"""

import glob
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.search.embed_text import build_embed_text
from app.search.tokenize import build_match_query

DB = "data/spec_query.db"
BACKUP_GLOB = "data/backups/spec_query.db.before_markup_fix_*"

QUERIES_NOISE = ["style", "td", "word", "mathrm", "center", "border"]
QUERIES_REAL = [
    "接头极限抗拉强度", "残余变形", "接头等级", "直螺纹接头", "最小拧紧扭矩值",
    "屈服强度", "钢筋机械连接", "单向拉伸", "套筒", "疲劳性能检验",
]


def load(path, table):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [(r["id"], r["search_text"] or "") for r in conn.execute(
        "SELECT id, search_text FROM clauses" if table == "cur"
        else "SELECT id, search_text FROM clauses")]
    conn.close()
    mem = sqlite3.connect(":memory:")
    mem.execute("CREATE VIRTUAL TABLE f USING fts5(search_text)")
    mem.executemany("INSERT INTO f(rowid, search_text) VALUES (?,?)", rows)
    return mem


def main():
    backup = sorted(glob.glob(BACKUP_GLOB))[-1]
    print(f"修复前库: {backup}")
    old = load(backup, "old")
    new = load(DB, "cur")

    def hits(conn, q):
        mq = build_match_query(q)
        if not mq:
            return set()
        try:
            return {r[0] for r in conn.execute("SELECT rowid FROM f WHERE f MATCH ?", (mq,))}
        except sqlite3.OperationalError:
            return set()

    print("\n=== 1. 标记噪声查询（应归零）===")
    print(f"  {'查询':<12}{'修复前':>8}{'修复后':>8}")
    for q in QUERIES_NOISE:
        print(f"  {q:<12}{len(hits(old, q)):>8}{len(hits(new, q)):>8}")

    print("\n=== 2. 真实业务查询（应保持/提升）===")
    print(f"  {'查询':<20}{'修复前':>8}{'修复后':>8}{'变化':>8}")
    tot_o = tot_n = 0
    for q in QUERIES_REAL:
        a, b = len(hits(old, q)), len(hits(new, q))
        tot_o += a
        tot_n += b
        delta = b - a
        print(f"  {q:<20}{a:>8}{b:>8}{('+' if delta > 0 else '') + str(delta) if delta else '0':>8}")
    print(f"  {'合计':<20}{tot_o:>8}{tot_n:>8}")

    print("\n=== 3. embedding 文本长度（向量不再由标记主导）===")
    print("  修复前 = 复刻旧路径（f-string 直接拼原始 content）；修复后 = 走 build_embed_text")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    bak = sqlite3.connect(backup)
    bak.row_factory = sqlite3.Row
    print(f"  {'clause':<10}{'修复前字符':>12}{'修复后字符':>12}{'压缩':>8}")
    for cid in (391, 393, 439, 440, 441, 373):
        ro = bak.execute(
            "SELECT c.content, c.clause_no, c.title, s.code, s.title st FROM clauses c "
            "JOIN specifications s ON c.spec_id=s.id WHERE c.id=?", (cid,)).fetchone()
        rn = conn.execute(
            "SELECT c.content, c.clause_no, c.title, s.code, s.title st FROM clauses c "
            "JOIN specifications s ON c.spec_id=s.id WHERE c.id=?", (cid,)).fetchone()
        if not ro or not rn:
            continue
        # 旧路径：build_embed_text 修复前的实现（原样拼 content）
        eo = (f"{ro['code'] or ''} {ro['st'] or ''} [{ro['clause_no']}] "
              f"{ro['title'] or ''} {ro['content'] or ''}").strip()
        en = build_embed_text(rn["code"], rn["st"], rn["clause_no"], rn["title"], rn["content"])
        print(f"  {cid:<10}{len(eo):>12}{len(en):>12}{(1 - len(en) / max(1, len(eo))):>7.0%}")
    bak.close()
    conn.close()


if __name__ == "__main__":
    rc = getattr(sys.stdout, "reconfigure", None)
    if callable(rc):
        rc(encoding="utf-8")
    main()
