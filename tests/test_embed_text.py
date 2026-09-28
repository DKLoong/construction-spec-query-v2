"""embedding 文本构造测试（build_embed_text 与旧拼接格式一致）"""
import re
from pathlib import Path

from app.search.embed_text import build_embed_text


def test_build_embed_text_includes_section_path_after_clause_no():
    """面包屑插在条文号之后、正文之前（保留位置信号）"""
    got = build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容",
                           "5 混凝土分项工程 > 5.1 模板")
    assert got == "GB 50010 混凝土规范 [5.1.1] 5 混凝土分项工程 > 5.1 模板 模板 内容"


def test_build_embed_text_without_section_path_unchanged():
    """不传面包屑时，输出与旧格式逐字一致（保证存量向量不因本改动而错位）"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容") == \
        build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容", "")
    # 与旧格式**字面量**逐字一致（上一条只证明「默认值 == 显式空串」，这一条
    # 才真正钉住「存量向量文本不漂移」）
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容") == \
        "GB 50010 混凝土规范 [5.1.1] 模板 内容"


def test_build_embed_text_full():
    """完整字段拼接结果与旧格式逐字节一致"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板", "内容") == \
        "GB 50010 混凝土规范 [5.1.1] 模板 内容"


def test_build_embed_text_matches_legacy_format():
    """非空字段下与导入/维护路由旧拼接格式一致"""
    code, spec_title, clause_no, title, content = "GB 50010", "混凝土规范", "5.1.1", "模板", "内容"
    legacy = f"{code or ''} {spec_title or ''} [{clause_no}] {title or ''} {content}"
    assert build_embed_text(code, spec_title, clause_no, title, content) == legacy


def test_build_embed_text_empty_fields_no_extra_space():
    """空 title/content 末尾 strip，不残留多余空格"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "", "") == \
        "GB 50010 混凝土规范 [5.1.1]"
    # 空 code/spec_title 开头无多余空格
    assert build_embed_text("", "", "5.1.1", "模板", "内容") == "[5.1.1] 模板 内容"


def test_build_embed_text_strips_markup_residue():
    """含 PaddleOCR-VL 标记的 content 不得把标记喂进 embedding 模型"""
    content = (
        '<div style="text-align: center;">表3.0.5 接头极限抗拉强度</div>'
        '<table border=1><tr><td>接头等级</td><td colspan="2">连接件型式</td></tr></table>'
        "抗拉强度 $ N/mm^{{2}} $ 应符合要求"
    )
    text = build_embed_text("JGJ 107", "钢筋机械连接技术规程", "3.0.5", "", content)
    assert "<" not in text and "$" not in text
    for bad in ("td", "style", "div", "table", "border", "colspan", "N/mm"):
        assert bad not in text, f"embedding 文本残留标记: {bad}"
    assert "接头极限抗拉强度" in text
    assert "接头等级" in text
    assert "连接件型式" in text
    assert "应符合要求" in text


def test_build_embed_text_plain_content_unchanged():
    """无标记 content 的 embedding 文本逐字节不变（保证既有向量不失效）"""
    assert build_embed_text("GB 50010", "混凝土规范", "5.1.1", "模板",
                            "套筒是钢筋机械连接的关键部件。") == \
        "GB 50010 混凝土规范 [5.1.1] 模板 套筒是钢筋机械连接的关键部件。"


# ═══════════════════════════════════════════
# 源码守卫：生产调用点不得漏传面包屑
# ═══════════════════════════════════════════

#: 生产调用点全表（`app/` 4 处 + `scripts/` 2 处）。
#: 实测依据：`grep -rn "build_embed_text" app/ scripts/ --include=*.py`
PRODUCTION_EMBED_CALLERS = [
    "app/routes/import_routes.py",       # 导入 phase2（量最大）
    "app/routes/maintenance_routes.py",  # 维护页全量重建
    "app/routes/spec_routes.py",         # 条文编辑重索引
    "app/search/vector_search.py",       # index_missing 补齐缺失向量
    "scripts/reindex_vectors.py",        # 重建脚本
    "scripts/probe_rebuild_effect.py",   # 重建效果探针
]

_CALL_RE = re.compile(r"build_embed_text\((?:[^()]|\([^()]*\))*\)")


def _top_level_arg_count(call_src: str) -> int:
    """数 call_src 的**顶层**实参个数（嵌套括号内的逗号不计）。

    只按 `,` 计数会被嵌套括号骗过：`c.get("section_path", "")` 是**一个**实参
    却含 2 个逗号——5 个实参的调用会被算成 7 个，`>= 6` 的判据当场失效。
    """
    inner = call_src[call_src.index("(") + 1:call_src.rindex(")")]
    if not inner.strip():
        return 0
    depth = 0
    n = 1
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            n += 1
    return n


def test_top_level_arg_count_ignores_nested_commas():
    """顶层实参计数不得被嵌套括号里的逗号骗过——否则 `>= 6` 判据会放行 5 参调用。

    `f(a, b, c, d, e.get("x", ""))` 是 **5** 个实参，朴素 `count(",") + 1` 会算成 6，
    于是「漏传第 6 参」被误判为已传。这正是本单元要防的静默漏改。
    """
    assert _top_level_arg_count("f(a, b, c, d, e)") == 5
    assert _top_level_arg_count('f(a, b, c, d, e.get("x", ""))') == 5
    assert _top_level_arg_count('f(a, b, c, d, e, g.get("x", ""))') == 6
    assert _top_level_arg_count("f()") == 0
    assert _top_level_arg_count("f(a)") == 1


def test_all_production_callers_pass_section_path():
    """生产调用点必须都**传**面包屑给 build_embed_text——漏改是静默的（新参数有默认值 ""）。

    ⚠ **C-10**：不得写成 `assert "section_path" in src`——那样任何提到该串的文件都能
    通过（`app/routes/import_routes.py` 因为要写该列必然含它），断言形同虚设。
    这里断言**调用形态**：每个调用点的实参含 `section_path`，或**顶层实参** ≥ 6 个
    （将来换个变量名传面包屑也不会误报）。
    """
    root = Path(__file__).resolve().parent.parent
    for rel in PRODUCTION_EMBED_CALLERS:
        src = (root / rel).read_text(encoding="utf-8")
        calls = [c for c in _CALL_RE.findall(src) if "def build_embed_text" not in c]
        assert calls, f"{rel} 未找到 build_embed_text 调用"
        for c in calls:
            assert "section_path" in c or _top_level_arg_count(c) >= 6, \
                f"{rel} 的调用未传面包屑（第 6 参缺失）: {c[:80]}"


def _has_call(src: str) -> bool:
    """源码里是否存在**调用**（而非 `def build_embed_text(...)` 定义本身）"""
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("def ", "from ", "import ")):
            continue
        if "build_embed_text(" in stripped:
            return True
    return False


def test_production_caller_list_is_complete():
    """守卫的目标清单必须与实际生产调用点**完全一致**——漏列一个文件 = 静默放行。

    本用例重扫 `app/` 与 `scripts/` 全部 .py，与硬编码清单对账；
    将来新增调用点却忘了登记时，这里会失败（提示把它加进 `PRODUCTION_EMBED_CALLERS`）。
    """
    root = Path(__file__).resolve().parent.parent
    found = {
        p.relative_to(root).as_posix()
        for base in ("app", "scripts")
        for p in (root / base).rglob("*.py")
        if _has_call(p.read_text(encoding="utf-8"))
    }
    listed = set(PRODUCTION_EMBED_CALLERS)
    assert found == listed, (
        f"生产调用点清单与实际不符：实际多出 {sorted(found - listed)}，"
        f"清单多出 {sorted(listed - found)}")
