"""一次性探针：全语料扫「本该是条文/结构节点、却静默降级成正文」的编号写法变体

判据独立于 parser 的 `_NUM_PATTERNS`（用宽松形态识别），因此能找出 parser 的
正则盲区，而不是自我确认。勿长期保留。

⚠️ **时效**（2026-09-29 入仓时的状态）：本探针记录的是**修复前**的判定与假设验证，对应的修复已落地（A 81add06 / B c585e84 / F4a 0fbf828 / F4b 4e18d86 / F5 0bac3c7）。保留它是为了回归对照与「同一形状再出现时能快速复现」；**精确的降级行计数以生产代码 `md_parser.find_degraded_heading_lines`（勘察指标 12）为准**，不要再把它当门禁。
"""
import glob
import re
import sys

sys.path.insert(0, '.')

import app.parser.md_parser as P
from app.parser.ocr_clean import clean_ocr_text

# 宽松形态：不问空格怎么排，只问"这看起来是不是一个编号/附录标题"
LOOSE = [
    ('附录+字母', re.compile(r'^附录\s*[A-Z]')),
    ('字母+点+数字', re.compile(r'^[A-Z]\s*[.．]\s*\d')),
    ('纯数字编号', re.compile(r'^\d+\s*[.．]\s*\d')),
    ('字母连写数字', re.compile(r'^[A-Z]{1,4}\s*\d')),
]


def _shape(s: str) -> str:
    """把行首编号的"形状"归一成可归类签名：数字→N、空白→␣、保留点与字母"""
    head = s.strip().lstrip('#').strip()[:14]
    head = re.sub(r'\d', 'N', head)
    head = re.sub(r'[\s　 ]', '␣', head)
    return head


def run():
    files = sorted(glob.glob('data/outputs/*/*.md'))
    grand: dict[str, list[tuple[str, int, str, str]]] = {}
    for path in files:
        raw = open(path, encoding='utf-8').read()
        md = clean_ocr_text(raw)
        lines = md.split('\n')
        st = P._ParseState()
        fails = []
        for i, l in enumerate(lines, 1):
            cand = P._candidate_of(l, st)
            if cand is not None:
                continue
            body = l.strip().lstrip('#').strip()
            if not body:
                continue
            # 目录点引行（含中文省略号）本就是"不该成候选"的正确行为，排除
            if re.search(r'[.．]{5,}|…{2,}', l):
                continue
            hit = next((name for name, pat in LOOSE if pat.match(body)), None)
            if not hit:
                continue
            # 只关心"像标题/编号行"的：带 # 前缀，或（非 # 且编号后紧跟非中文内容）
            is_hash = l.lstrip().startswith('#')
            if not is_hash:
                # 非 # 行必须有"编号 token 内部含异常空白"这一特征，否则是正常正文
                lead = re.match(r'^[A-Z\d]+[\s.．]{1,4}\d', body)
                if not lead or ' ' not in lead.group(0).strip() or not re.search(r'[A-Z\d]\s+[.．\d]|[.．]\s+\d', body[:12]):
                    continue
                if not re.match(r'^[A-Z\d]', body):
                    continue
            # 排除非条文块（前言/条文说明/公告/用词说明）——它们本就不该是普通条文
            if P.is_non_clause_title(P._clean_title(P._extract_title(body)) or body):
                continue
            fails.append((hit, i, _shape(l), body[:44]))
        if fails:
            grand[path] = fails
        print(f'--- {path}: 降级行 {len(fails)} ---')
        agg: dict[tuple[str, str], int] = {}
        for hit, i, shp, body in fails:
            agg[(hit, shp)] = agg.get((hit, shp), 0) + 1
        for (hit, shp), n in sorted(agg.items(), key=lambda x: -x[1])[:8]:
            print(f'    {hit:<10} 形状={shp!r:<20} ×{n}')

    print('\n═══ 全语料形状汇总 ═══')
    total: dict[str, int] = {}
    for hit, i, shp, body in [x for v in grand.values() for x in v]:
        total[shp] = total.get(shp, 0) + 1
    for shp, n in sorted(total.items(), key=lambda x: -x[1]):
        print(f'  {shp!r:<22} ×{n}')
    print(f'\n合计降级行 {sum(total.values())}')


if __name__ == '__main__':
    run()
