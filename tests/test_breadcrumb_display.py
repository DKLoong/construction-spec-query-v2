"""Task 13（U9）回归：面包屑展示两处落点 + 检索过滤区帮助文案

对应用户实测的两条反馈：
1. 详情弹窗「来源：」行看不到所属节 → 现在显示该条的完整 `section_path`
2. 结果列表每行看不出条文属于哪一节 → 每行显示**该行自身**的 `section_path`

设计约束（控制器裁定，见 task-13）：列表落点必须**无状态** —— 每行自带完整路径，
不得与上一行比较、不得插入独立「节分隔行」。故除「两行各带自己的路径」外，另加：
- 一条**分页切片**用例：翻到第 2 页（page_size=1）时该行仍带自己的路径——若有人
  改成「与前一行比较后决定是否显示」，切片后的首行会拿不到上下文，本用例即失败；
- 一条**相邻两行路径相同**用例：原夹具两条路径互不相同，「与前一行相同则隐藏」这类
  相邻行依赖退化照样通过（R38②）；
- 一条**容器结构**用例 + `_rows()` 内的同类断言：`_rows()` 只按开标签切分，插在两行
  之间的独立分隔元素察觉不到，故按顶层子元素核对（同上）。

另两条为用户实测第 5 项（勾选框说明文案）与静态资源版本号：
- 2026-09-29 用户裁定**回到 tooltip**（常驻文字把本就拥挤的左栏又挤高一行）⇒ 说明文案
  由**单一变量**承载，`title` 悬停与 input 的 `aria-label` 同源引用——a11y 不再靠
  "删掉说明"解决（`title` 对键盘/触屏不显示，故必须另有一条等价通道）；
  QA 页由 `qa.js` 追加一句问题文本兜底（该兜底只存在于 QA 链路，检索页写了就是撒谎）
- 帮助文案不得含「目次」（属「直接过滤」类，导入时整段丢弃、从不入库）
"""
import re
from pathlib import Path

import pytest

from app.database import get_db, init_db

_ROOT = Path(__file__).resolve().parent.parent
_PARTIALS = _ROOT / "app" / "templates" / "partials"

_SECTION_DETAIL = "5 混凝土分项工程 > 5.2 钢筋"
_SECTION_A = "3 基本规定 > 3.2 材料"
_SECTION_B = "4 模板分项工程 > 4.3 安装"

# app.css 版本号下限：`.clause-path` 截断样式随本次改动引入（原为 26）
_APP_CSS_MIN_VERSION = 27


# ── 夹具与工具 ──────────────────────────────────────────────

def _esc(s: str) -> str:
    """Jinja 自动转义后的形态（面包屑含 `>`，必须转义为 &gt;）"""
    return s.replace("&", "&amp;").replace(">", "&gt;").replace("<", "&lt;")


def _seed(db_name: str, monkeypatch, tmp_path, rows: list[tuple]) -> dict:
    """建临时库 + 写条文，返回 `{clause_id: section_path}`。

    ⚠ 与 tests/test_lifecycle_display.py 同一套路：`auth_client` 已在
    `tmp_path/test_auth.db` 建库并登录（cookie 已就位），这里换到**另一个**库文件，
    鉴权只看 JWT、不再回查用户表，故不需要在新库里重建管理员。
    """
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / db_name))
    init_db()
    id_to_path = {}
    with get_db() as conn:
        conn.execute("INSERT INTO specifications (code, title) VALUES ('CJJ 2', '市政桥梁')")
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for no, title, path in rows:
            conn.execute(
                "INSERT INTO clauses (spec_id, clause_no, title, content, section_path)"
                " VALUES (?, ?, ?, ?, ?)",
                (spec_id, no, title, f"{title}的正文内容。", path),
            )
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            id_to_path[cid] = path
    return id_to_path


_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>")

# void 元素：没有闭合标签，不得计入深度（否则标签配平会被整段带偏）
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "source", "track", "wbr",
})

# 结果列表容器（result_content.html 里的 wrapper，内含「共找到 N 条结果」与各结果行）
_LIST_OPEN_RE = re.compile(r'<div class="result-list"[^>]*>')


def _list_top_level_children(html: str) -> list[str]:
    """返回 `.result-list` 容器**顶层**子元素（各自完整的 HTML 片段）。

    为什么需要它：`_rows()` 只是按 `class="result-item"` 的开标签切分，**插在两行
    之间的独立分隔元素**会落进前一行的尾巴里，切分结果看不出任何异常。而用户
    决策（R7）要求列表落点**无状态**——每行自带完整路径、不得插入独立「节分隔行」。
    故必须另做一次结构遍历，「插了分隔元素」才看得见。

    只做标签配平（不引入 HTML 解析依赖）：void 元素与自闭合标签不计深度。
    """
    m0 = _LIST_OPEN_RE.search(html)
    assert m0, "响应中找不到结果列表容器（.result-list）"
    depth = 1                      # 已进入容器
    child_start = m0.end()
    children: list[str] = []
    for m in _TAG_RE.finditer(html, m0.end()):
        name = m.group(2).lower()
        if m.group(1):             # 闭合标签
            if name in _VOID_TAGS:
                continue
            depth -= 1
            if depth == 0:
                break              # 容器闭合
            if depth == 1:         # 刚闭合一个顶层子元素
                children.append(html[child_start:m.end()])
                child_start = m.end()
        elif name in _VOID_TAGS or m.group(3):
            if depth == 1:         # 顶层的 void/自闭合元素本身就是「多出来的东西」
                children.append(m.group(0))
                child_start = m.end()
        else:
            depth += 1
    return [c for c in children if "<" in c]


def _rows(html: str) -> list[str]:
    """按结果项切分页面，返回各结果行的 HTML 片段。

    ⚠ 先核对容器的**顶层子元素**（见 `_list_top_level_children`）：切分本身察觉不到
    插在两行之间的独立分隔元素（它只会落进前一行的尾巴）。这一步让**本文件所有
    用例**对「插入节分隔行」当场失败——R7 的无状态约束要的正是这个。
    """
    kids = _list_top_level_children(html)
    assert kids, "结果列表容器内没有任何顶层元素"
    header, rows = kids[0], kids[1:]
    assert header.lstrip().startswith("<p>") and "共找到" in header, \
        f"结果列表首个顶层元素应为「共找到 N 条结果」：{header[:60]!r}"
    for k in rows:
        assert k.lstrip().startswith('<div class="result-item"'), \
            f"结果列表出现非结果行的顶层元素（R7 禁止插入独立「节分隔行」）：{k[:80]!r}"
    return rows


def _clause_id(frag: str) -> int:
    """取结果行的 data-clause-id（缺则显式失败，避免 None 静默传播）"""
    m = re.search(r'data-clause-id="(\d+)"', frag)
    assert m, f"结果行缺少 data-clause-id：{frag[:80]!r}"
    return int(m.group(1))


def _read_partial(name: str) -> str:
    return (_PARTIALS / name).read_text(encoding="utf-8")


def _css_rule(selector: str) -> str:
    css = (_ROOT / "static" / "app.css").read_text(encoding="utf-8")
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"app.css 缺少规则：{selector}"
    return m.group(1)


# ── 落点①：详情弹窗 ─────────────────────────────────────────

def test_clause_detail_shows_full_section_path(auth_client, monkeypatch, tmp_path):
    """详情「来源：」行显示该条的完整面包屑，且经 Jinja 自动转义（不得 |safe）"""
    id_to_path = _seed("bd_detail.db", monkeypatch, tmp_path,
                       [("5.2.1", "原材料", _SECTION_DETAIL)])
    cid = next(iter(id_to_path))
    resp = auth_client.get(f"/clause/{cid}")
    assert resp.status_code == 200
    assert _esc(_SECTION_DETAIL) in resp.text, "详情应显示完整 section_path"
    assert 'class="clause-path"' in resp.text, "面包屑应有 clause-path 标记（供样式/测试定位）"
    # 反证：一旦改用 |safe，转义形态消失、原文出现
    assert _SECTION_DETAIL not in resp.text, "面包屑不得 |safe（`>` 必须被转义）"


@pytest.mark.parametrize("empty", ["", None], ids=["empty-string", "null"])
def test_clause_detail_empty_section_path_renders_no_separator(
        auth_client, monkeypatch, tmp_path, empty):
    """空面包屑 ⇒ 整个 span 不渲染，且「来源：」行不留尾随分隔符"""
    tag = "null" if empty is None else "empty"
    id_to_path = _seed(f"bd_detail_{tag}.db", monkeypatch, tmp_path,
                       [("1.0.1", "总则", empty)])
    cid = next(iter(id_to_path))
    resp = auth_client.get(f"/clause/{cid}")
    assert 'class="clause-path"' not in resp.text, "空面包屑不得渲染空 span"
    m = re.search(r"来源：(.*?)</small>", resp.text, re.S)
    assert m, "详情页应仍有「来源：」行"
    assert m.group(1).strip() == "市政桥梁", \
        f"空面包屑不得留分隔符，实得：{m.group(1)!r}"


# ── 落点②：结果列表每行 ────────────────────────────────────

def test_result_rows_each_carry_their_own_section_path(auth_client, monkeypatch, tmp_path):
    """每行显示**该行自身**的面包屑：行数=条文数，且不串入别行的路径"""
    id_to_path = _seed("bd_rows.db", monkeypatch, tmp_path,
                       [("3.2.1", "钢筋", _SECTION_A), ("4.3.1", "模板安装", _SECTION_B)])
    resp = auth_client.get("/search?all=1")
    frags = _rows(resp.text)
    assert len(frags) == len(id_to_path) == 2, \
        "结果行数应等于条文数（不得插入独立「节分隔行」）"
    for frag in frags:
        cid = _clause_id(frag)
        assert _esc(id_to_path[cid]) in frag, f"行 {cid} 未带自己的面包屑"
        for other_id, other_path in id_to_path.items():
            if other_id != cid:
                assert _esc(other_path) not in frag, f"行 {cid} 串入了行 {other_id} 的面包屑"


def test_result_row_section_path_survives_pagination_slice(auth_client, monkeypatch, tmp_path):
    """无状态落点的实证：翻到第 2 页（page_size=1）时该行仍带自己的路径

    相邻行依赖（「与上一行比较后再决定显示」）在切片后必然拿不到上下文，
    届时本用例失败——这就是选用无状态写法的原因。
    """
    id_to_path = _seed("bd_page.db", monkeypatch, tmp_path,
                       [("3.2.1", "钢筋", _SECTION_A), ("4.3.1", "模板安装", _SECTION_B)])
    resp = auth_client.get("/search?all=1&page_size=1&page=2")
    frags = _rows(resp.text)
    assert len(frags) == 1, "第 2 页应只有 1 行"
    cid = _clause_id(frags[0])
    assert _esc(id_to_path[cid]) in frags[0], "分页切片后的行仍须自带面包屑"


def test_adjacent_rows_with_same_section_path_both_render_it(auth_client, monkeypatch, tmp_path):
    """相邻两行路径**相同**时，两行都必须各自渲染自己的路径。

    原夹具两条路径互不相同 ⇒「与前一行相同则隐藏」这类**相邻行依赖**退化照样通过
    （R38②）。本用例把两行设成同一路径：第 2 行一旦去比较上一行，它就会被隐藏、本条
    失败。用户决策（R7）要的就是无状态落点。
    """
    id_to_path = _seed("bd_rows_same.db", monkeypatch, tmp_path,
                       [("3.2.1", "钢筋", _SECTION_A), ("3.2.2", "钢筋接头", _SECTION_A)])
    resp = auth_client.get("/search?all=1")
    frags = _rows(resp.text)
    assert len(frags) == len(id_to_path) == 2, "应恰好渲染 2 行结果"
    for frag in frags:
        assert _esc(_SECTION_A) in frag, "相邻两行路径相同，也必须各带自己的面包屑"
    assert resp.text.count('class="clause-path"') == 2, \
        "两行路径相同 ⇒ 仍应有 2 个 clause-path 元素（不得省略第 2 个）"


def test_result_list_renders_no_separator_element(auth_client, monkeypatch, tmp_path):
    """列表容器的顶层子元素只允许「共找到 N 条结果」一行 + 各结果行。

    无状态约束（R7）的**结构面**：不得插入独立「节分隔行」。`_rows()` 按开标签切分，
    插进去的分隔元素会落进前一行的尾巴、切分结果毫无异常 ⇒ 这里直接核对容器结构。
    """
    _seed("bd_rows_struct.db", monkeypatch, tmp_path,
          [("3.2.1", "钢筋", _SECTION_A), ("4.3.1", "模板安装", _SECTION_B)])
    resp = auth_client.get("/search?all=1")
    kids = _list_top_level_children(resp.text)
    assert len(kids) == 3, \
        f"顶层应为「共找到」一行 + 2 行结果，实得 {len(kids)} 个：{[k[:60] for k in kids]}"
    assert "共找到" in kids[0], f"首个顶层元素应为「共找到 N 条结果」：{kids[0][:60]!r}"
    for k in kids[1:]:
        assert k.lstrip().startswith('<div class="result-item"'), \
            f"结果列表出现非结果行的顶层元素：{k[:80]!r}"


def test_result_row_without_section_path_renders_nothing(auth_client, monkeypatch, tmp_path):
    """空面包屑的行不渲染任何占位（不留空 div，也不留分隔符）

    ⚠ 原断言 `assert " > " not in resp.text` 是**恒真**的：结果行本就不渲染任何分隔符
    （` · ` 分隔符只在详情模板里），模板怎么写都成立（R38①）。判据改为**行内没有
    clause-path 元素**——模板若退化成无条件渲染（空串也出 div），本条即失败。
    """
    _seed("bd_rows_empty.db", monkeypatch, tmp_path, [("1.0.1", "总则", "")])
    resp = auth_client.get("/search?all=1")
    assert 'class="clause-path"' not in resp.text, "空面包屑不得渲染空 div（整页口径）"
    frags = _rows(resp.text)
    assert len(frags) == 1, "应恰好渲染 1 行结果"
    assert "1.0.1" in frags[0], "取到的应正是该结果行（含其条文号）"
    assert "clause-path" not in frags[0], "空面包屑的行渲染了 clause-path 占位"


# ── 勾选框说明（tooltip + QA 页独有的兜底句） ───────────────────

def _non_clause_hint() -> str:
    """取出说明文案变量 `{% set non_clause_hint = ... %}` 的值（单一来源）。"""
    m = re.search(r'\{%\s*set\s+non_clause_hint\s*=\s*"([^"]+)"\s*%\}',
                  _read_partial("tree_panel.html"))
    assert m, "说明文案应由单一变量承载（否则 title 与 aria-label 会各自漂移）"
    return m.group(1)


def test_include_non_clause_hint_is_tooltip_not_always_visible():
    """说明走 `title` 悬停 tooltip（设计文档 §4.5），不再是常驻可见文字。

    2026-09-28（d3a899a）曾把 title 换成常驻 `<small>`，理由是「title 对键盘 focus 与
    触屏均不显示」——方向对，但违背设计且把左栏挤高一行；2026-09-29 用户裁定回到 tooltip。
    a11y 改由 `aria-label` 承担，**同一个变量**同时喂两处，故说明不会只剩一条通道。
    """
    html = _read_partial("tree_panel.html")
    # 常驻文字撤下（含它的 aria-describedby 目标——留着就是死引用）
    assert "include-non-clause-help" not in html, "说明不得再常驻渲染/被引用"
    assert 'class="field-hint"' not in html, "左栏不得再有常驻说明元素"
    hint = _non_clause_hint()
    # 悬停通道：title 挂 label（覆盖整行，与相邻「启用 CE 精排」同形）
    label = re.search(r'<label[^>]*for="include-non-clause"[^>]*>', html)
    assert label, "应找到 for=include-non-clause 的 label"
    assert 'title="{{ non_clause_hint }}"' in label.group(0), \
        "勾选框缺 title 悬停 tooltip"
    # 键盘/触屏通道：aria-label 挂**可聚焦的 input**（挂 label 上不会随聚焦播报）
    inp = re.search(r'<input[^>]*x-model="includeNonClause"[^>]*>', html, re.S)
    assert inp, "应找到 include_non_clause 复选框 input"
    assert 'aria-label="包含非条文内容（{{ non_clause_hint }}）"' in inp.group(0), \
        "input 缺 aria-label 等价说明（title 对键盘/触屏不显示）"
    # 说明文案须真的进到这两处（不是引用了空变量）
    assert "前言" in hint and "引用标准名录" in hint, f"说明文案不完整：{hint!r}"
    # 检索页不得宣称 QA 链路才有的问题文本兜底（该句由 qa.js 只在 QA 页追加）
    assert "若提问中含" not in html and "AI 问答中即使不勾选" not in html, \
        "该兜底只在 QA 链路存在，不得写进两页共用的 partial"


def test_include_non_clause_hint_lists_no_toc():
    """帮助文案不得含「目次」——目次属「直接过滤」类，导入时整段丢弃、从不入库"""
    from app.parser.md_parser import _NON_CLAUSE_FILTER_TITLES
    # 前置事实（若该集合变了，本条断言的前提也就没了）
    assert "目次" in _NON_CLAUSE_FILTER_TITLES
    hint = _non_clause_hint()
    assert "目次" not in hint and "Contents" not in hint, \
        f"帮助文案不得宣称包含目次（实际导入时被丢弃）：{hint!r}"
    assert "前言" in hint, "帮助文案应列出真正入库的非条文类别"


# ── 样式与静态资源版本号 ────────────────────────────────────

def test_clause_path_truncation_is_css_only():
    """长面包屑的截断/省略必须纯 CSS（列表行不换行 + 省略号），不引入 JS 逻辑"""
    rule = _css_rule(".result-item .clause-path")
    assert "nowrap" in rule, "列表行面包屑应单行显示"
    assert "overflow" in rule and "hidden" in rule
    assert "ellipsis" in rule, "超长应省略号截断"


def test_static_version_bumped_for_app_css():
    """改了 static/app.css 必须递增 base.html 的 ?v=N（否则浏览器吃缓存旧样式）"""
    base = (_ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    m = re.search(r"/static/app\.css\?v=(\d+)", base)
    assert m, "base.html 应有带版本号的 app.css 引用"
    assert int(m.group(1)) >= _APP_CSS_MIN_VERSION, \
        f"app.css 版本号应 ≥ {_APP_CSS_MIN_VERSION}（本次改动引入 .clause-path）"
