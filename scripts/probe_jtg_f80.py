"""一次性探针：定位 JTG F80/1-2017 导入的三个症状（勿长期保留）

用法：D:/Python/python.exe -c "from scripts.probe_jtg_f80 import run; run()"

⚠️ **时效**（2026-09-29 入仓时的状态）：本探针记录的是**修复前**的判定与假设验证，对应的修复已落地（A 81add06 / B c585e84 / F4a 0fbf828 / F4b 4e18d86 / F5 0bac3c7）。保留它是为了回归对照与「同一形状再出现时能快速复现」；**精确的降级行计数以生产代码 `md_parser.find_degraded_heading_lines`（勘察指标 12）为准**，不要再把它当门禁。
"""
import re
import sys

sys.path.insert(0, '.')

from app.parser.md_parser import parse_markdown, _candidate_of, _ParseState, _vote_title_mode
from app.parser.ocr_clean import clean_ocr_text

MD = 'data/outputs/8376e02c/8376e02c.md'


def run():
    raw = open(MD, encoding='utf-8').read()
    md = clean_ocr_text(raw)
    lines = md.split('\n')
    clauses = parse_markdown(md)
    print(f'源行数 {len(lines)}  →  解析条文 {len(clauses)} 条')

    # ── 症状 1：目录行漏成条文（title/content 带点引/省略号 + 页码）
    print('\n═══ 症状1：疑似目录行落库 ═══')
    toc_like = [c for c in clauses
                if re.search(r'[.．…]{2,}\s*\d+\s*$', (c['title'] or '') + (c['content'] or ''))]
    for c in toc_like:
        print(f"  {c['clause_no']!r:12} title={c['title']!r:28} content={c['content'][:60]!r}")
    print(f'  小计 {len(toc_like)} 条')

    # 源侧：目次段（`### 目次` 到正文首条）里，哪些行成了候选行？
    toc_start = next(i for i, l in enumerate(lines) if l.strip().lstrip('#').strip() == '目次')
    toc_end = 229  # 正文 `## 1 总则` 附近
    print(f'\n  目次段源行 {toc_start+1}~{toc_end}，其中"成候选行"的：')
    st = _ParseState()
    n_cand = 0
    for i in range(toc_start, toc_end):
        cand = _candidate_of(lines[i], st)
        if cand:
            n_cand += 1
            if n_cand <= 50:
                print(f'    L{i+1}: {lines[i].strip()!r}')
    print(f'  小计候选行 {n_cand} 行')

    # ── 症状 2：附录标题未成节点
    print('\n═══ 症状2：附录标题行是否为候选行 ═══')
    st2 = _ParseState()
    miss = []
    for i, l in enumerate(lines):
        if l.lstrip().startswith('#') and '附录' in l:
            t = l.lstrip('#').strip()
            cand = _candidate_of(l, st2)
            ok = cand is not None and ('附录' in cand[1] or cand[1][0].isalpha())
            if not ok:
                miss.append((i + 1, t[:40]))
    print(f'  未成候选行的附录标题 {len(miss)} 处（前 25）：')
    for ln, t in miss[:25]:
        print(f'    L{ln}: {t!r}')

    print('\n  → 正文侧附录节点的 clause_no：')
    ap = [c['clause_no'] for c in clauses if c['clause_no'].startswith('附录') or c['clause_no'][:1].isalpha()]
    print(f'    {ap[:40]}')

    print('\n  → 疑似被吞的附录标题（content 里出现裸 `附录 X ` 行）：')
    for c in clauses:
        for ln in (c['content'] or '').split('\n'):
            if re.match(r'^附录\s*[A-Z]\s', ln.strip()):
                print(f"    {c['clause_no']!r:12} content 内含 {ln.strip()[:36]!r}")
                break

    print('\n  → 附录区各条 section_path 抽样：')
    for c in clauses:
        if re.match(r'^[B-Z]\.0\.\d+$', c['clause_no']):
            print(f"    {c['clause_no']!r:10} section_path={c['section_path']!r}")
        if len([1 for _ in ()]) : pass

    # ── 症状 3：条文说明 术语 2.x
    print('\n═══ 症状3：条文说明段 2.x 条文 ═══')
    seen_commentary = False
    for c in clauses:
        pass
    rows = [c for c in clauses if re.match(r'^2\.0\.\d+$', c['clause_no'])]
    for c in rows:
        print(f"  {c['clause_no']!r:8} title={c['title']!r:30} non={c['is_non_clause']} "
              f"parent_path={c['parent_path']} content={c['content'][:40]!r}")

    # 源侧：条文说明段里 2.x 标题行
    ci = next(i for i, l in enumerate(lines) if l.strip() == '条文说明')
    print(f'\n  条文说明段起始 L{ci+1}；段内所有 `2.0.x` 字样：')
    for i in range(ci, len(lines)):
        if '2.0.' in lines[i]:
            print(f'    L{i+1}: {lines[i].strip()[:70]!r}')

    # ── 两趟不变量快查
    print('\n═══ 预扫/主循环 stack 一致性（抽样） ═══')
    tm = _vote_title_mode(lines)
    print(f'  投票组数 {len(tm)}')


if __name__ == '__main__':
    run()
