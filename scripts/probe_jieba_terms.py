"""一次性探针：术语词典对 jieba 切分与 FTS5 召回的影响

⚠️ THROWAWAY —— 本脚本是一次性可行性探针，产出「一份结论」，不是要保留的业务代码。
   不入 app/ 任何模块，不写库，不产生迁移。结论采纳与否见 T5 spec。

待答问题：给 jieba 加工程术语词典，对检索是净收益还是净损失？

实验设计（三组索引 + 对照查询，全部只读）：
  1. 收益面：lexicon canonical 里有多少「本该一个词、现在被切碎」，即词典该收多少。
  2. 切分对照：真实条文 baseline（默认词典）vs treatment（+术语词典）的切分差异。
  3. 召回损失（核心）：对每个术语 T 的每个原子 token s，比较 MATCH "s" 在
     base / treat / dual（双写）三组索引上的命中集合差集。
  4. 双写补偿：dual = treatment token + baseline token 全量并集，验证损失是否归零 + 膨胀率。

用法：PYTHONUTF8=1 D:/Python/python.exe scripts/probe_jieba_terms.py
"""

import re
import sqlite3
import sys
from collections import defaultdict

import jieba

DB_PATH = "data/spec_query.db"
TOKEN_RE = re.compile(r"\W+")

# 本语料（JGJ 107-2016 钢筋机械连接技术规程）里高频出现、且明显应作整体术语的领域词。
# 用于验证「lexicon canonical 作为词源是否够用」——缺失即说明词源需要补。
DOMAIN_TERMS = [
    "钢筋机械连接", "机械连接", "接头百分率", "屈服强度", "抗拉强度",
    "混凝土保护层", "抗震设防烈度", "钢筋接头", "冷轧带肋钢筋",
    "锥螺纹接头", "直螺纹接头", "挤压接头", "熔融金属", "残余变形",
    "最大力总伸长率", "单向拉伸", "高应力反复拉压", "大变形反复拉压",
]


def make_tokenizer(terms=None):
    """独立 Tokenizer 实例，不污染全局 jieba（生产侧全局 jieba 无任何自定义词）。"""
    tk = jieba.Tokenizer()
    for t in terms or []:
        tk.add_word(t)
    return tk


def tokenize(tk, text):
    """复刻 app/search/tokenize.tokenize。"""
    out = []
    for w in tk.cut(text or "", cut_all=False):
        w = w.strip()
        if w and not TOKEN_RE.fullmatch(w):
            out.append(w)
    return out


def build_search_text(tk, clause_no, title, content):
    """复刻 app/search/tokenize.build_search_text。"""
    parts = tokenize(tk, f"{title or ''} {content or ''}")
    if clause_no and clause_no.strip():
        parts.append(clause_no.strip())
    return " ".join(parts) or " "


def build_match_query(tk, keyword, join_with="AND"):
    """复刻 app/search/tokenize.build_match_query。"""
    toks = tokenize(tk, keyword)
    if not toks:
        return ""
    return f" {join_with} ".join('"' + t.replace('"', '""') + '"' for t in toks)


def load_clauses():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [
        (r["id"], r["clause_no"] or "", r["title"] or "", r["content"] or "")
        for r in conn.execute(
            "SELECT id, clause_no, title, content FROM clauses ORDER BY id"
        )
    ]
    canon = [
        r["canonical"].strip()
        for r in conn.execute(
            "SELECT DISTINCT canonical FROM lexicon_entries "
            "WHERE is_active=1 AND canonical IS NOT NULL AND TRIM(canonical) <> ''"
        )
    ]
    conn.close()
    return rows, canon


def derive_terms(canon, base_tk):
    """收益面：canonical 里被默认词典切碎（>1 token）的多字词 = 词典应收的术语。"""
    merged, already = [], []
    for c in canon:
        if len(c) < 3 or not re.search(r"[\u4e00-\u9fff]", c):
            continue  # 太短或纯非中文，作术语无意义
        if len(tokenize(base_tk, c)) > 1:
            merged.append(c)
        else:
            already.append(c)
    return merged, already


def build_fts(pairs):
    """pairs: [(clause_id, search_text)] → 内存 FTS5 表，rowid=clause_id。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE fts USING fts5(search_text)")
    conn.executemany("INSERT INTO fts(rowid, search_text) VALUES (?, ?)", pairs)
    return conn


def hits(conn, query):
    if not query:
        return set()
    return {r[0] for r in conn.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (query,))}


def section(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main():
    base_tk = make_tokenizer()
    clauses, canon = load_clauses()
    merged_terms, already_terms = derive_terms(canon, base_tk)

    section("第 0 步：语料与词源")
    print(f"条文数            : {len(clauses)}")
    print(f"lexicon canonical : {len(canon)} 条（含重复口径去重后）")
    print(f"  已是单 token    : {len(already_terms)}")
    print(f"  被切碎（收益面）: {len(merged_terms)}  ← 词典应收的术语数")
    missing = [t for t in DOMAIN_TERMS if t not in merged_terms]
    print(f"\n手工领域词 {len(DOMAIN_TERMS)} 个中，lexicon 未覆盖 {len(missing)} 个：")
    print("  " + ("、".join(missing) if missing else "（无）"))

    # 词典 = 自动派生术语 ∪ 手工领域词
    terms = sorted(set(merged_terms) | set(DOMAIN_TERMS))
    treat_tk = make_tokenizer(terms)

    section("第 1 步：切分对照（三组索引文本）")
    base_pairs, treat_pairs, dual_pairs = [], [], []
    tok_delta = []
    changed = 0
    for cid, cno, title, content in clauses:
        st_b = build_search_text(base_tk, cno, title, content)
        st_t = build_search_text(treat_tk, cno, title, content)
        st_d = f"{st_t} {st_b}"  # 双写 = treatment ∪ baseline 全量并集
        base_pairs.append((cid, st_b))
        treat_pairs.append((cid, st_t))
        dual_pairs.append((cid, st_d))
        nb, nt = len(st_b.split()), len(st_t.split())
        tok_delta.append(nb - nt)
        if st_b.split() != st_t.split():
            changed += 1
    print(f"切分发生变化的条文: {changed}/{len(clauses)}")
    print(f"token 净变化      : 合并省下 {sum(tok_delta)} 个 token（正数=省下）")
    print(f"索引字符膨胀(双写): {sum(len(d) for _, d in dual_pairs) / max(1, sum(len(b) for _, b in base_pairs)):.2f}x")

    fts_b = build_fts(base_pairs)
    fts_t = build_fts(treat_pairs)
    fts_d = build_fts(dual_pairs)

    section("第 2 步：召回损失（子词查询）——核心风险面")
    print("对每个术语 T 的每个「默认切分下的原子 token」s，比较 MATCH \"s\" 的命中集合。")
    print("修正：只统计 len(s)>=2 的真实查询词（单字如「大」「高」非真实查询，计入会虚高损失率）。\n")
    total_lost = total_gained = total_before = 0
    raw_lost = raw_before = 0
    per_term = []
    losses_detail = defaultdict(list)
    for T in terms:
        subs = tokenize(base_tk, T)
        if len(subs) < 2:
            continue  # 术语本身未被切碎，无合并 → 无损失可言
        lost_t = gained_t = before_t = 0
        for s in subs:
            qb, qt = build_match_query(base_tk, s), build_match_query(treat_tk, s)
            if not qb or not qt:
                continue
            hb, ht = hits(fts_b, qb), hits(fts_t, qt)
            lost = len(hb - ht)
            raw_lost += lost
            raw_before += len(hb)
            if len(s) < 2:
                continue  # 单字查询非真实场景，不计入修正口径
            lost_t += lost
            gained_t += len(ht - hb)
            before_t += len(hb)
            if lost:
                losses_detail[s].append((T, lost))
        total_lost += lost_t
        total_gained += gained_t
        total_before += before_t
        if lost_t or gained_t:
            per_term.append((T, "/".join(subs), before_t, lost_t, gained_t))

    per_term.sort(key=lambda x: -x[3])
    print(f"{'术语':<16}{'默认切分':<28}{'原命中':>7}{'丢失':>7}{'新增':>7}")
    print("-" * 72)
    for T, subs, before, lost, gained in per_term[:25]:
        print(f"{T:<16}{subs:<28}{before:>7}{lost:>7}{gained:>7}")
    if len(per_term) > 25:
        print(f"... 另有 {len(per_term) - 25} 个术语有变化")
    print("-" * 72)
    print(f"{'合计(修正)':<16}{'':<28}{total_before:>7}{total_lost:>7}{total_gained:>7}")
    print(f"{'合计(含单字)':<16}{'':<28}{raw_before:>7}{raw_lost:>7}{'':>7}")
    if total_before:
        print(f"\n修正后子词查询丢失率: {total_lost / total_before:.1%}"
              f"（丢失 {total_lost} / 原命中 {total_before}）")

    print("\n按「被伤及的子词」归因（哪些真实查询词因术语原子化而丢召回）：")
    print(f"{'子词':<10}{'受影响术语数':>12}{'丢失条次':>10}   示例术语")
    print("-" * 72)
    for s, items in sorted(losses_detail.items(), key=lambda kv: -sum(n for _, n in kv[1]))[:15]:
        print(f"{s:<10}{len(items):>12}{sum(n for _, n in items):>10}   "
              + "、".join(T for T, _ in items[:3]))

    section("第 3 步：全术语查询（精度面）")
    print("查询整个术语 T 时：baseline 用 AND 松匹配 vs treatment 单 token 精匹配。\n")
    print(f"{'术语':<18}{'AND松匹配':>10}{'精匹配':>9}{'噪声差':>9}")
    print("-" * 72)
    tot_loose = tot_precise = 0
    rows3 = []
    for T in terms:
        qb = build_match_query(base_tk, T)
        qt = build_match_query(treat_tk, T)
        if qb == qt:
            continue  # 切分一致，无差异
        hb, ht = hits(fts_b, qb), hits(fts_t, qt)
        tot_loose += len(hb)
        tot_precise += len(ht)
        rows3.append((T, len(hb), len(ht), len(hb) - len(ht)))
    rows3.sort(key=lambda x: -x[3])
    for T, loose, precise, noise in rows3[:20]:
        print(f"{T:<18}{loose:>10}{precise:>9}{noise:>9}")
    print("-" * 72)
    print(f"合计 松匹配 {tot_loose} → 精匹配 {tot_precise}（剔除噪声 {tot_loose - tot_precise}）")

    section("第 4 步：双写补偿是否归零损失")
    total_dual_lost = total_dual_gained = 0
    for T in terms:
        subs = tokenize(base_tk, T)
        if len(subs) < 2:
            continue
        for s in subs:
            qb = build_match_query(base_tk, s)
            hb, hd = hits(fts_b, qb), hits(fts_d, qb)  # 双写索引上仍用 baseline 查询串
            total_dual_lost += len(hb - hd)
            total_dual_gained += len(hd - hb)
    print(f"双写索引 vs baseline：丢失 {total_dual_lost} 条次，新增 {total_dual_gained} 条次")
    print("（丢失=0 即双写对子词查询无损；新增>0 说明双写还额外放大了召回）")

    section("第 5 步：索引/查询分词器错配矩阵")
    print("4 种组合 × 全术语查询命中数。验证「索引侧与查询侧必须同词典」这一不变量。\n")
    print(f"{'术语':<14}{'baseline索引':>24}{'treatment索引':>24}{'dual索引':>22}")
    print(f"{'':<14}{'默认':>12}{'词典':>12}{'默认':>12}{'词典':>12}{'默认':>11}{'词典':>11}")
    print("-" * 84)
    mism_totals = {"b/b": 0, "b/t": 0, "t/b": 0, "t/t": 0, "d/b": 0, "d/t": 0}
    for T in ["钢筋机械连接", "机械连接", "屈服强度", "残余变形", "直螺纹接头"]:
        qb = build_match_query(base_tk, T)
        qt = build_match_query(treat_tk, T)
        c = {
            "b/b": len(hits(fts_b, qb)),
            "b/t": len(hits(fts_b, qt)),
            "t/b": len(hits(fts_t, qb)),
            "t/t": len(hits(fts_t, qt)),
            "d/b": len(hits(fts_d, qb)),
            "d/t": len(hits(fts_d, qt)),
        }
        for k in mism_totals:
            mism_totals[k] += c[k]
        print(f"{T:<14}{c['b/b']:>12}{c['b/t']:>12}{c['t/b']:>12}{c['t/t']:>12}"
              f"{c['d/b']:>11}{c['d/t']:>11}")
    print("-" * 84)
    print(f"{'合计':<14}{mism_totals['b/b']:>12}{mism_totals['b/t']:>12}"
          f"{mism_totals['t/b']:>12}{mism_totals['t/t']:>12}"
          f"{mism_totals['d/b']:>11}{mism_totals['d/t']:>11}")
    print("\n读法：错配列（baseline索引+词典查询 / treatment索引+默认查询）命中趋近 0")
    print("      → 索引侧与查询侧必须同词典，不能只改一边。")
    print("      dual索引「词典」列 = 精确召回（同 treatment），dual 列子词查询见第 4 步（零损失）。")

    section("第 6 步：具体条文实例")
    print("取 1.0.1（本文档最典型条文），看切分与查询的实际差异：\n")
    cid, cno, title, content = clauses[1]
    print(f"原文（clause_no={cno}）: {content[:80]}")
    print(f"\nbaseline  : {build_search_text(base_tk, cno, title, content)[:150]}")
    print(f"\ntreatment : {build_search_text(treat_tk, cno, title, content)[:150]}")
    print(f"\ndual      : {build_search_text(treat_tk, cno, title, content)[:100]} + [baseline 全量]")
    for s in ["钢筋", "机械连接", "钢筋机械连接"]:
        qb = build_match_query(base_tk, s)
        qt = build_match_query(treat_tk, s)
        print(f"\n查询「{s}」: baseline索引={cid in hits(fts_b, qb)}  "
              f"treatment索引={cid in hits(fts_t, qt) if qt else 'N/A'}  "
              f"dual索引={cid in hits(fts_d, qb)}")

    section("结论速览")
    print(f"收益面：{len(terms)} 个术语进词典（lexicon 派生 {len(merged_terms)} + 手工补 {len(DOMAIN_TERMS)}）")
    print(f"      切分变化条文 {changed}/{len(clauses)}，合并省下 {sum(tok_delta)} token")
    print(f"风险面：子词查询丢失 {total_lost}/{total_before}" + (f" = {total_lost / total_before:.1%}" if total_before else ""))
    print(f"      （含单字子词口径：{raw_lost}/{raw_before}）")
    print(f"      双写后丢失 {total_dual_lost}")
    print(f"精度面：全术语查询剔除松散噪声 {tot_loose - tot_precise} 条次")

    fts_b.close()
    fts_t.close()
    fts_d.close()


if __name__ == "__main__":
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    jieba.setLogLevel(60)  # 静音 jieba 首次构建词典的日志
    main()
