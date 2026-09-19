"""生成词库同词跨组冲突的逐簇对照清单（只读，不改数据）

用途：数据清理前的**人工过审材料**。输出 docs/lexicon-conflict-review-<date>.md。
本脚本只读；实际清理必须由用户逐簇裁决后另行执行。

方法：
1. 把冲突词作为"边"连接各词条行 → 并查集求连通分量（一个词跨两组会把它们连起来，
   可能形成长链，故必须按簇而非按对处理）
2. 对每簇分类并给建议：
   - SAME_SET   两行词集相同（同一等价关系、方向相反）→ 建议合并，canonical 取规范词侧
   - CHAIN      一行 canonical 落在另一行词集里（同义链 A~B, B~C）→ 建议合并为一组
   - AMBIGUOUS  词集交叉/上下位（如 石子 同属"石子组"与"粗骨料组"）→ **须人工裁决**
                此类合并会造成语义错误（碎石、卵石是子类，非粗骨料的同义词）
   - BIG        簇内 >2 行 → 须人工裁决
"""

import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB = "data/spec_query.db"
EQUIV = ("synonym", "alias")


def load_rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, canonical, variants, note, created_at "
        "FROM lexicon_entries WHERE kind IN (?, ?) AND is_active = 1 ORDER BY id",
        EQUIV)]


def words_of(row):
    return {row["canonical"]} | {
        v.strip() for v in (row["variants"] or "").split(",") if v.strip()}


class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def build_clusters(rows):
    """按「同一个词出现在多行」连接 → 连通分量"""
    owner = defaultdict(list)
    for r in rows:
        for w in sorted(words_of(r)):
            owner[w].append(r["id"])
    dsu = DSU()
    for r in rows:
        dsu.find(r["id"])
    for w, ids in owner.items():
        for i in ids[1:]:
            dsu.union(ids[0], i)
    clusters = defaultdict(list)
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        clusters[dsu.find(r["id"])].append(by_id[r["id"]])
    # 只保留真的含冲突（同词跨行）的簇
    out = []
    for members in clusters.values():
        seen = defaultdict(set)
        for r in members:
            for w in words_of(r):
                seen[w].add(r["id"])
        if any(len(v) > 1 for v in seen.values()):
            out.append(members)
    return out, owner


def classify(members):
    """返回 (类型, 建议说明)"""
    if len(members) > 2:
        return "BIG", "簇内 >2 行，须人工裁决（可能牵连出很大的同义链）"
    a, b = members
    wa, wb = words_of(a), words_of(b)
    if wa == wb:
        alias = a if a["kind"] == "alias" else (b if b["kind"] == "alias" else a)
        return ("SAME_SET",
                f"同词汇、方向相反 → 建议合并为单组，canonical 取规范词侧「{alias['canonical']}」"
                f"（alias 批 2026-09-06 为规范词方向，符合 spec）")
    # 链：一行 canonical 出现在另一行词集里
    if a["canonical"] in wb or b["canonical"] in wa:
        link = a["canonical"] if a["canonical"] in wb else b["canonical"]
        return ("CHAIN", f"同义链（「{link}」是连接点）→ 建议合并为一组")
    return ("AMBIGUOUS",
            "词集交叉/上下位关系 → **须人工裁决**；直接合并会造出语义错误的大组")


# 需人工裁决的 7 簇：给出我的倾向（含 2 簇建议「拆分而非合并」）
_MANUAL = {
    "天花板": "倾向**合并**：顶棚={天棚,天花板,吊顶,天花}。语义风险低（顶棚/吊顶常互换），"
              "但严格说吊顶是悬吊式顶棚的一种做法，故列为需你确认。",
    "混凝土浇筑": "倾向**合并**：canonical=混凝土浇筑（现行规范用词），variants=混凝土浇注,砼浇筑。"
                "三方本就是同一概念，204/205 各自把「旧写法」「俗称」当 canonical 是错的。风险极低。",
    "承包单位": "倾向**合并**：canonical=施工单位，variants=承包单位,乙方。"
              "工程检索语境下用户多查「施工单位」。注意法律上承包单位/施工单位有细微差别，请确认。",
    "卷帘门": "倾向**合并**：canonical=防火卷帘，variants=卷帘门,卷闸门。"
            "严格说是上下位（防火卷帘⊂卷帘门），但施工规范检索中用户查「卷帘门」多半就关心防火卷帘。",
    "满堂架": "倾向**合并**：满堂脚手架={满堂支架,满堂架,满堂红脚手架}。四者同一支撑体系，风险低。",
    "安全带": "⚠️ 倾向**拆分，不要合并**：安全带与安全绳在规范里是**两个不同产品**"
            "（坠落悬挂安全带 vs 分立的绳，配套但不等同）。建议 156 行移除「安全绳」"
            "（保留 安全带=保险带），433 行 安全绳=生命绳 独立成组，432 与 156 合并。",
    "石子": "⚠️ 倾向**删除 220 行，不要合并**：220 把「碎石,卵石」当作「石子」的同义词，"
          "实为**上下位**（碎石/卵石 是 粗骨料 的子类）。建议保留 325 粗骨料={石子,粗集料}"
          "与 329 碎石={轧轧碎岩石} 各自成组。",
}


def concrete_action(members):
    """返回 (保留行, 删除行列表, 合并后 variants 或 None)"""
    ctype = classify(members)[0]
    if ctype not in ("SAME_SET", "CHAIN"):
        return None, [], None

    def rank(r):
        # 优先保留 alias（规范词方向符合 spec），其次 id 小的
        return (0 if r["kind"] == "alias" else 1, r["id"])

    keep = sorted(members, key=rank)[0]
    drop = [r for r in members if r["id"] != keep["id"]]
    merged = sorted({w for r in members for w in words_of(r)} - {keep["canonical"]})
    return keep, drop, merged


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = load_rows(conn)
    conn.close()

    clusters, owner = build_clusters(rows)
    dup_words = {w: ids for w, ids in owner.items() if len(ids) > 1}

    kinds = defaultdict(int)
    for m in clusters:
        kinds[classify(m)[0]] += 1

    lines = []
    lines.append(f"# 词库同词跨组冲突 — 逐簇对照清单（{date.today()}）")
    lines.append("")
    lines.append("> 生成脚本：`scripts/report_lexicon_conflicts.py`（只读，不改数据）")
    lines.append("> 背景：`lexicon_entries` 的读侧校验要求「一个词只能属于一个等价组」，"
                 "违反即整表作废（fail-closed）。2026-09-06 一批 alias 导入造成下列冲突，"
                 "使 720 条 active 词条全部失效 13 天。")
    lines.append("")
    lines.append(f"- active equiv 词条：**{len(rows)}** 行")
    lines.append(f"- 冲突词面：**{len(dup_words)}** 个")
    lines.append(f"- 冲突簇：**{len(clusters)}** 个（按连通分量聚合）")
    lines.append(f"  - SAME_SET（可直接合并）: {kinds['SAME_SET']}")
    lines.append(f"  - CHAIN（建议合并）: {kinds['CHAIN']}")
    lines.append(f"  - AMBIGUOUS（须裁决）: {kinds['AMBIGUOUS']}")
    lines.append(f"  - BIG（须裁决）: {kinds['BIG']}")
    mech_keep = mech_del = 0
    for m in clusters:
        keep, drop, _ = concrete_action(m)
        if keep is not None:
            mech_keep += 1
            mech_del += len(drop)
    lines.append("")
    lines.append(f"- **机械可清理**：{kinds['SAME_SET'] + kinds['CHAIN']} 簇 → "
                 f"保留 {mech_keep} 行（含 variants 并集）、删除 {mech_del} 行")
    lines.append(f"- **须你裁决**：{kinds['AMBIGUOUS'] + kinds['BIG']} 簇"
                 f"（各簇末尾有「我的倾向」；其中 2 簇我建议拆分/删除而非合并）")
    lines.append("")

    order = {"SAME_SET": 0, "CHAIN": 1, "AMBIGUOUS": 2, "BIG": 3}
    clusters.sort(key=lambda m: (order[classify(m)[0]], m[0]["id"]))

    for i, members in enumerate(clusters, 1):
        ctype, advice = classify(members)
        shared = sorted(w for w in
                        {w for r in members for w in words_of(r)}
                        if len({r["id"] for r in members if w in words_of(r)}) > 1)
        lines.append(f"## 簇 {i} [{ctype}] — 冲突词：{'、'.join(shared)}")
        lines.append("")
        lines.append("| 行 id | kind | canonical | variants | 创建时间 | note |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in members:
            lines.append(f"| {r['id']} | {r['kind']} | {r['canonical']} | "
                         f"{r['variants'] or ''} | {(r['created_at'] or '')[:19]} | "
                         f"{(r['note'] or '')[:24]} |")
        lines.append("")
        lines.append(f"**建议**：{advice}")
        keep, drop, merged = concrete_action(members)
        if keep is not None:
            lines.append("")
            lines.append(f"**建议操作**：保留 id={keep['id']}（canonical={keep['canonical']}，"
                         f"kind={keep['kind']}）"
                         + (f"；variants 改为 `{','.join(merged)}`" if merged is not None else "")
                         + f"；删除 id={','.join(str(r['id']) for r in drop)}")
        manual = next((v for k, v in _MANUAL.items() if k in shared), None)
        if manual:
            lines.append("")
            lines.append(f"**我的倾向（须你裁决）**：{manual}")
        lines.append("")

    out = Path("docs") / f"lexicon-conflict-review-{date.today()}.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print(f"词条 {len(rows)} 行 / 冲突词 {len(dup_words)} 个 / 冲突簇 {len(clusters)} 个")
    print(f"  SAME_SET={kinds['SAME_SET']}  CHAIN={kinds['CHAIN']}  "
          f"AMBIGUOUS={kinds['AMBIGUOUS']}  BIG={kinds['BIG']}")
    print(f"已写出：{out}")
    print()
    print("=== 簇规模分布（越大越需谨慎）===")
    sizes = defaultdict(int)
    for m in clusters:
        sizes[classify(m)[0] + f" / {len(m)}行"] += 1
    for k, v in sorted(sizes.items()):
        print(f"  {k}: {v} 簇")


if __name__ == "__main__":
    rc = getattr(sys.stdout, "reconfigure", None)
    if callable(rc):
        rc(encoding="utf-8")
    main()
