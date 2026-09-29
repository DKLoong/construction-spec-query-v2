"""降级行自检：结构标题没被识别出来时必须**有声**

背景（2026-09-29，JTG F80/1-2017 实况）：OCR 把 `### 附录 A …` 写成带空格的形态后，
整行被降级为普通正文、折进上一条（`13.4.3` 吞掉 10,923 字符），导入**不报错、进度条
正常、条文数看起来也只差几条** —— 用户是翻条文时人工发现的。这类失效模式下，
"没报警"比"报错"更贵。

本文件锁定三件事：
  1. `find_degraded_heading_lines` 能认出「形如结构标题、却未成候选行」的行；
  2. 该计数进 `scripts/survey_structure.py` 的回归指标（指标 12）；
  3. 导入链路（`_process_import_phase2`）命中时必须落 WARN 进 system_logs。
"""
from app.database import get_db, init_db
from app.parser.md_parser import find_degraded_heading_lines


# ── 判据本体 ──────────────────────────────────────────────────────────────

def test_bare_number_heading_is_reported():
    """`#### 4.2.1`（只有编号、无标题文本）→ 报出

    这是 JTG F80/1 条文说明的 83 行实况形态：编号后换行才是说明文字，
    而三条编号正则都要求「编号 + 空白 + 文本」⇒ 该行不成候选行，
    4.2.1 的说明被折进上一条 `4.2`。
    """
    md = ("## 4 路基土石方工程\n\n"
          "### 4.2 土方路基\n\n"
          "#### 4.2.1\n\n"
          "（1）明确地表清理范围。\n")
    assert find_degraded_heading_lines(md) == [(5, "#### 4.2.1", "bare_clause_no")]


def test_bare_appendix_marker_is_reported():
    """`附录A` 单独成行（标题在下一行）同样是降级形态（旧语料实测 3 行）"""
    md = "1.0.1 正文甲。\n\n附录A\n\n接头试件试验方法\n"
    hits = find_degraded_heading_lines(md)
    assert [(n, t) for n, t, _ in hits] == [(3, "附录A")]


def test_recognized_and_non_heading_lines_are_not_reported():
    """判据不得恒真：已识别的条文、目录点引行都不算降级行"""
    md = (
        "## 1 总则\n\n"
        "1.0.1 正文甲。\n\n"
        "## 1.1 术语\n\n"
        "1.1.1 正文乙。\n\n"
        "2 术语 ..... 3\n"          # 目录：点号点引
        "6.11 导流工程……28\n"        # 目录：中文省略号点引
    )
    assert find_degraded_heading_lines(md) == []


def test_cross_reference_split_by_line_break_is_not_reported():
    """被换行劈开的交叉引用不是降级行（GB 50086-2015 实测 `A. 4.1)。永久性压力…`）

    与真降级行的区别：编号 token 之后紧跟标点、**没有**空白分隔的正文。
    少了这条收窄，检测器会在 GB 50086-2015 上产生 7 条假报，指标随即失去可信度。
    """
    md = ("## 4 锚杆\n\n"
          "A. 4.1)。永久性压力分散型锚杆应满足要求。\n\n"
          "4.0.1 正文甲。\n")
    assert find_degraded_heading_lines(md) == []


def test_clean_document_reports_nothing():
    """完全正常的文档必须为空（否则告警会因噪声被忽略）"""
    md = "# 某规范\n\n## 1 总则\n\n1.0.1 正文。\n\n## 2 术语\n\n2.0.1 检验。\n"
    assert find_degraded_heading_lines(md) == []


# ── 指标 12 ───────────────────────────────────────────────────────────────

def test_survey_reports_degraded_heading_rows():
    """勘察脚本产出 degraded_heading_rows，值等于判据命中的行数"""
    from scripts import survey_structure as ss

    md = ("## 4 路基土石方工程\n\n### 4.2 土方路基\n\n#### 4.2.1\n\n（1）说明甲。\n\n"
          "#### 4.2.2\n\n（1）说明乙。\n")
    assert ss.survey_structure(md)["degraded_heading_rows"] == 2


# ── 导入链路 ──────────────────────────────────────────────────────────────

def _run_import(monkeypatch, tmp_path, name: str, md: str):
    """跑完整 `_process_import_phase2` 并回读 system_logs（隔离手法同
    test_import_vector_failure：临时库 + 临时输出/上传/lance 目录）。

    端到端而非只测判据函数：判据存在但没接在导入链路上（函数没人调用）是这类
    "自检"最常见的半成品形态，只有走完整链路才证明得了。
    """
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / f"{name}.db"))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / f"out_{name}"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / f"up_{name}"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / f"lance_{name}"))
    init_db()

    import app.routes.import_routes as ir
    ir.progress_store[name] = {"status": "processing", "progress": 0}
    ir._process_import_phase2(name, md, "某规范", "JTG TEST-2026",
                              str(tmp_path / f"{name}.md"), f"hash_{name}")
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT level, action FROM system_logs").fetchall()]


def test_import_path_writes_warning_into_system_logs(monkeypatch, tmp_path):
    """导入命中降级行 → system_logs 落 WARN，级别必须是 'WARN'

    级别不是装饰：日志 UI 的「⚠️ 异常」筛选（`ERROR,WARN`）与告警着色只认 `WARN`，
    模块 logger 桥接写的 `WARNING` 在界面上不可见（已实测）。自检的全部价值就是
    "能被看见"，故这里把级别也钉住。
    """
    md = "## 4 路基土石方工程\n\n#### 4.2.1\n\n（1）明确地表清理范围。\n"
    rows = _run_import(monkeypatch, tmp_path, "deg_warn", md)

    warns = [r for r in rows if r["level"] == "WARN"]
    assert any("结构标题" in r["action"] for r in warns), (
        f"降级行未落 WARN 进 system_logs：{rows}"
    )


def test_import_path_stays_quiet_on_clean_document(monkeypatch, tmp_path):
    """干净文档不得留这条 WARN —— 告警必须稀缺，否则会被忽略"""
    md = "# 某规范\n\n## 1 总则\n\n1.0.1 正文。\n\n## 2 术语\n\n2.0.1 检验。\n"
    rows = _run_import(monkeypatch, tmp_path, "deg_quiet", md)

    assert not any("结构标题" in r["action"] for r in rows), (
        f"干净文档也报了降级行：{[r for r in rows if '结构标题' in r['action']]}"
    )
