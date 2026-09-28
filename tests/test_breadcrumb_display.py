"""Task 13（U9）回归：面包屑展示两处落点 + 检索过滤区帮助文案

对应用户实测的两条反馈：
1. 详情弹窗「来源：」行看不到所属节 → 现在显示该条的完整 `section_path`
2. 结果列表每行看不出条文属于哪一节 → 每行显示**该行自身**的 `section_path`

设计约束（控制器裁定，见 task-13）：列表落点必须**无状态** —— 每行自带完整路径，
不得与上一行比较、不得插入独立「节分隔行」。故除「两行各带自己的路径」外，另加一条
**分页切片**用例：翻到第 2 页（page_size=1）时该行仍带自己的路径——若有人改成
「与前一行比较后决定是否显示」，切片后的首行会拿不到上下文，本用例即失败。

另两条为用户实测第 5 项（勾选框说明文案）与静态资源版本号：
- `title` 属性对键盘 focus 与触屏均不显示，屏幕阅读器支持也不一致 → 改可见文字 + aria
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


def _rows(html: str) -> list[str]:
    """按结果项切分页面（每项以 class="result-item" 的开标签起算）"""
    return html.split('<div class="result-item"')[1:]


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


def test_result_row_without_section_path_renders_nothing(auth_client, monkeypatch, tmp_path):
    """空面包屑的行不渲染任何占位（不留空 div）"""
    _seed("bd_rows_empty.db", monkeypatch, tmp_path, [("1.0.1", "总则", "")])
    resp = auth_client.get("/search?all=1")
    assert 'class="clause-path"' not in resp.text, "空面包屑不得渲染空 div"
    assert " > " not in resp.text, "空面包屑不得留分隔符"


# ── 帮助文案（tooltip → 可见文字 + aria） ───────────────────

def test_include_non_clause_help_is_visible_text_not_title():
    """勾选框说明须为可见文字并挂到可聚焦的 input 上（title 对键盘/触屏不可见）"""
    html = _read_partial("tree_panel.html")
    # 旧 title 说明整段撤下
    assert "勾选后放行前言/条文说明等打标非条文" not in html, "不得再用 title 承载说明"
    # 检索页不得再串 AI 问答页的措辞：该「问题文本兜底」只在 QA 链路生效
    assert "若提问中含" not in html, "检索页不得宣称 QA 链路才有的问题文本兜底"
    # 说明须可见（<small>），并作为 input 的描述（挂 label 上不会随聚焦播报）
    assert '<small id="include-non-clause-help" class="field-hint">' in html, \
        "说明应为可见的 <small> 元素"
    inp = re.search(r'<input[^>]*x-model="includeNonClause"[^>]*>', html, re.S)
    assert inp, "应找到 include_non_clause 复选框 input"
    assert 'aria-describedby="include-non-clause-help"' in inp.group(0), \
        "aria-describedby 必须挂在可聚焦的 input 上"


def test_include_non_clause_help_lists_no_toc():
    """帮助文案不得含「目次」——目次属「直接过滤」类，导入时整段丢弃、从不入库"""
    from app.parser.md_parser import _NON_CLAUSE_FILTER_TITLES
    # 前置事实（若该集合变了，本条断言的前提也就没了）
    assert "目次" in _NON_CLAUSE_FILTER_TITLES
    m = re.search(r'<small id="include-non-clause-help"[^>]*>(.*?)</small>',
                  _read_partial("tree_panel.html"), re.S)
    assert m, "应找到帮助文案元素"
    help_text = m.group(1)
    assert "目次" not in help_text and "Contents" not in help_text, \
        f"帮助文案不得宣称包含目次（实际导入时被丢弃）：{help_text!r}"
    assert "前言" in help_text, "帮助文案应列出真正入库的非条文类别"


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
