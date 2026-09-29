"""降级行自检：结构标题没被识别出来时必须**有声**

背景（2026-09-29，JTG F80/1-2017 实况）：OCR 把 `### 附录 A …` 写成带空格的形态后，
整行被降级为普通正文、折进上一条（`13.4.3` 吞掉 10,923 字符），导入**不报错、进度
正常、条文数只差几条** —— 用户是翻条文时人工发现的。这类失效模式里，"没报警"比
"报错"更贵。

本文件锁定三件事：
  1. `find_degraded_heading_lines` 能认出「形如结构标题、却未成候选行」的行；
  2. 该计数进 `scripts/survey_structure.py` 的回归指标（指标 12）；
  3. 导入链路（`_process_import_phase2`）命中时必须落 WARN 进 system_logs。

⚠️ 判据的**可失败性**是这里的重点：检测器若用解析器那套正则，就只能看见两者的
重叠区 —— 而重叠区恰好是空的（解析器认了的行不算降级、按设计拒了的行必须排除），
于是一旦出现正则不认识的变体，两边同时失明、指标纹丝不动。故本文件的**正例**一律
取「解析器不认识、但人眼一看就是结构标题」的形态（当前是**全角字母**：
`附录 Ａ`、`Ｂ.１.２`）。半角的三类变体（编号内空格 / 裸编号标题 / 字母 O 冒充 0）
已在 2026-09-29 这一轮修完，改列在反例里钉住。
"""
from app.database import get_db, init_db
from app.parser.md_parser import find_degraded_heading_lines


# 解析器当前**不认识**、但确实是结构标题的形态（检测器的正例来源）。
# ⚠️ 为什么用全角字母：半角变体（编号内空格、裸编号标题、字母 O 冒充 0）都已在
#    2026-09-29 这一轮修完，**再拿它们当正例就是在测已修好的东西**；全角字母
#    （`Ａ`/`Ｂ`）是同类里尚未覆盖的一种，也是"未来变体会不会被发现"的真实样本。
_FULLWIDTH_APPENDIX = "### 附录 Ａ 单位、分部及分项工程的划分"
_FULLWIDTH_LETTER_NUMBER = "Ｂ.１.２ 说明行内容。"


def _clean_md_around(*lines: str) -> str:
    """把给定行包进一份最小可解析的文档里。"""
    return "## 13 声屏障工程\n\n13.4.3 外观质量应符合下列规定：\n\n" + "\n\n".join(lines) + "\n"


# ── 判据本体：正例（解析器的盲区） ──────────────────────────────────────

def test_fullwidth_letter_appendix_is_reported():
    """全角字母 `附录 Ａ`：解析器三条编号正则都只认半角 `[A-Z]` ⇒ 整行降级"""
    hits = find_degraded_heading_lines(_clean_md_around(_FULLWIDTH_APPENDIX))
    assert [(n, t, r) for n, t, r in hits] == [
        (5, _FULLWIDTH_APPENDIX, "unrecognized_shape")
    ]


def test_fullwidth_letter_and_digit_token_is_reported():
    """全角字母 + 全角数字编号（`Ｂ.１.２`）：解析器的半角 `[A-Z]` 正则不认 ⇒ 整行降级

    全角**数字**其实已被 Python 的 `\\d`（Unicode 十进制数字）覆盖，被漏的是
    **全角字母**。它与 `附录 Ａ` 属同一类，但走的是字母编号支而非附录支。
    """
    hits = find_degraded_heading_lines(_clean_md_around(_FULLWIDTH_LETTER_NUMBER))
    assert [(n, t) for n, t, _ in hits] == [(5, _FULLWIDTH_LETTER_NUMBER)]


# ── 判据本体：反例（不得恒真，也不得随实现漂移成"什么都报"） ────────────

def test_forms_fixed_this_round_are_no_longer_reported():
    """本轮修好的写法不得再被报出

    `附录 A`（编号内空格）、`B. 0.1`（点后空格）（以上 A）、`#### 4.2.1`（裸编号标题，
    F4a）、`M. O. 2`（字母 O 冒充 0，F5）修好后都不再是降级行 —— 这条反例钉住
    「修复真的生效」，防止检测器变成报什么都对的恒真断言。
    """
    md = _clean_md_around(
        "### 附录 A 单位、分部及分项工程的划分",
        "B. 0.1 路基和路面基层的压实度应以重型击实标准为准。",
        "#### 4.2.1",
        "M. O. 2 试验及计算方法应符合现行标准的规定。",
    )
    assert find_degraded_heading_lines(md) == []


def test_recognized_and_non_heading_lines_are_not_reported():
    """已识别的条文、目录点引行（两种点引写法）都不算降级行"""
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

    md = _clean_md_around(_FULLWIDTH_APPENDIX, _FULLWIDTH_LETTER_NUMBER)
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
    rows = _run_import(monkeypatch, tmp_path, "deg_warn",
                       _clean_md_around(_FULLWIDTH_APPENDIX, _FULLWIDTH_LETTER_NUMBER))

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
