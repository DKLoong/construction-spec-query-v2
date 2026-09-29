"""一次性探针：最小假设验证——把「编号内空格」「中文省略号点引」两处正则放宽后重解析

不改仓库代码，只在内存里替换 md_parser 的模块级常量，观察三症状是否消失。
勿长期保留。

⚠️ **时效**（2026-09-29 入仓时的状态）：本探针记录的是**修复前**的判定与假设验证，对应的修复已落地（A 81add06 / B c585e84 / F4a 0fbf828 / F4b 4e18d86 / F5 0bac3c7）。保留它是为了回归对照与「同一形状再出现时能快速复现」；**精确的降级行计数以生产代码 `md_parser.find_degraded_heading_lines`（勘察指标 12）为准**，不要再把它当门禁。
"""
import re
import sys

sys.path.insert(0, '.')

import app.parser.md_parser as P
from app.parser.ocr_clean import clean_ocr_text

MD = 'data/outputs/8376e02c/8376e02c.md'


def _parse(md):
    return P.parse_markdown(md)


def _stats(clauses, tag):
    toc_like = [c for c in clauses
                if re.search(r'[.．…]{2,}\s*\d+\s*$', (c['title'] or '') + (c['content'] or ''))]
    ap = [c['clause_no'] for c in clauses if c['clause_no'].startswith('附录')]
    numeric = [c['clause_no'] for c in clauses
               if re.match(r'^[A-Z]\.', c['clause_no']) and not c['clause_no'][1:2].isdigit()]
    swallowed = [(c['clause_no'], ln.strip()[:30])
                 for c in clauses
                 for ln in (c['content'] or '').split('\n')
                 if re.match(r'^附录\s*[A-Z]\s', ln.strip())]
    print(f'[{tag}] 条文总数={len(clauses)}  目次样张={len(toc_like)}  '
          f'附录节点={ap}  含空格的字母条号={numeric}')
    if swallowed:
        print(f'      仍被吞的附录标题 {len(swallowed)} 处：{[s[0] for s in swallowed]}')
    for c in clauses:
        if re.match(r'^[B-Z]\.0\.\d+$', c['clause_no']) and c['clause_no'] in ('B.0.1', 'B.0.2'):
            print(f"      {c['clause_no']} section_path={c['section_path']!r} L={len(c['content'])}")
    return toc_like, ap, swallowed


def run():
    raw = open(MD, encoding='utf-8').read()
    md = clean_ocr_text(raw)

    orig_patterns = list(P._NUM_PATTERNS)
    orig_dot = P._DOT_LEADER

    print('═══ 基线（当前仓库代码） ═══')
    _stats(_parse(md), 'base')

    # 假设 1：编号内的空格不属于语义 —— 三处允许 \s*
    print('\n═══ 假设1：_NUM_PATTERNS 容忍编号内空格 ═══')
    P._NUM_PATTERNS[:] = [
        r'^(附录\s*[A-Z]+(?:[\s.．]*[\d]+)*)\s+(.+)',
        r'^([A-Z]+(?:[\s.．]?\d+)+)\s+(.+)',
        r'^(\d+(?:[\s.．]\d+)*)\s+(.+)',
    ]
    # 编号归一化：剥掉编号内部空白（_match_clause_line / _extract_clause_no 各自 replace ．）
    _stats(_parse(md), 'fix1')
    P._NUM_PATTERNS[:] = orig_patterns

    # 假设 2：中文省略号也是点引
    print('\n═══ 假设2：_DOT_LEADER 覆盖 `……`(U+2026) ═══')
    P._DOT_LEADER = re.compile(r'([.．]{5,}|…{2,})')
    _stats(_parse(md), 'fix2')
    P._DOT_LEADER = orig_dot

    # 两者同时
    print('\n═══ 两者同时 ═══')
    P._NUM_PATTERNS[:] = [
        r'^(附录\s*[A-Z]+(?:[\s.．]*[\d]+)*)\s+(.+)',
        r'^([A-Z]+(?:[\s.．]?\d+)+)\s+(.+)',
        r'^(\d+(?:[\s.．]\d+)*)\s+(.+)',
    ]
    P._DOT_LEADER = re.compile(r'([.．]{5,}|…{2,})')
    toc, ap, sw = _stats(_parse(md), 'both')

    # ── 直接验证三条代表性源行的候选判定
    print('\n═══ 逐行判定（放宽后） ═══')
    P._NUM_PATTERNS[:] = orig_patterns
    P._DOT_LEADER = orig_dot
    for s in ['### 附录 A 单位、分部及分项工程的划分', 'B. 0.1 路基和路面基层…',
              '6.11 导流工程……28  ', 'B.0.2 标准密度应做平行试验…']:
        st = P._ParseState()
        print(f'  基线 {s[:34]!r:38} → {P._candidate_of(s, st)}')
    P._NUM_PATTERNS[:] = [
        r'^(附录\s*[A-Z]+(?:[\s.．]*[\d]+)*)\s+(.+)',
        r'^([A-Z]+(?:[\s.．]?\d+)+)\s+(.+)',
        r'^(\d+(?:[\s.．]\d+)*)\s+(.+)',
    ]
    P._DOT_LEADER = re.compile(r'([.．]{5,}|…{2,})')
    for s in ['### 附录 A 单位、分部及分项工程的划分', 'B. 0.1 路基和路面基层…',
              '6.11 导流工程……28  ', 'B.0.2 标准密度应做平行试验…']:
        st = P._ParseState()
        print(f'  放宽 {s[:34]!r:38} → {P._candidate_of(s, st)}')


if __name__ == '__main__':
    run()
