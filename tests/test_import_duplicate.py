"""导入判重（编号 / 名称）测试

**用户口径（2026-09-30 裁定）**：
- 以**编号或名称**判重来防止重复导入；
- **状态不参与判重** —— 同一本规范不可能有两种状态。库中同码的废止版与现行版并存
  属历史记录，不该被当作「不同规范」，也不该因此放过重复导入（实测库里已出现
  `CJJ 2-2008` 废止 + 现行 两本，条文逐条完全相同）；
- 命中后是**警告 + 可确认继续**，不是硬拒绝 —— 用更清晰的文件重导同一本是合法需求
  （用户当天 08:25 就为了修正状态重导过一次）。

与既有的「重复文件」判重（`file_hash` = 上传字节的 SHA256）是**互补**关系：
同文件是确定性重复，同编号/同名称是疑似重复，两者文案与判定分开。
"""
import io

from app.database import get_db, init_db
from app.routes.import_routes import (
    _normalize_title_for_dup, find_duplicate_specs,
)

_MD = "# 测试规范\n\n## 1 总则\n\n### 1.1 一般规定\n\n混凝土施工应满足设计要求。\n"


def _seed_spec(tmp_path, monkeypatch, code, title, status="现行", file_hash=None):
    """建隔离库并插入一条规范（判重查询类用例共用）"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "dup.db"))
    init_db()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO specifications
                   (code, title, dim1_hierarchy, dim1_nature, status, file_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (code, title, "国家标准", "强制性", status, file_hash),
        )


def _insert(code, title, status="现行"):
    """向当前（已 patch 的）库插入一条规范"""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature, status)
               VALUES (?, ?, ?, ?, ?)""",
            (code, title, "行业标准", "强制性", status),
        )


def _patch_pipeline_paths(monkeypatch, tmp_path):
    """C-4：导入管线还会写向量与上传/输出目录，必须按**消费方模块名** patch"""
    monkeypatch.setattr(
        "app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance_db"))
    monkeypatch.setattr(
        "app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        "app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "outputs"))
    for d in ("lance_db", "uploads", "outputs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════
# 名称归一
# ═══════════════════════════════════════════

def test_normalize_title_folds_space_and_fullwidth():
    """空白与全角差异必须被折叠（用户预览里的空格用例）"""
    assert _normalize_title_for_dup("钢筋机械连接技术规程 ") == \
        _normalize_title_for_dup("钢筋机械连接技术规程")
    assert _normalize_title_for_dup("ＪＧＪ１０７") == _normalize_title_for_dup("JGJ107")


def test_normalize_title_strips_version_paren():
    """末尾版本括号被剥掉：『…规程（2015年版）』与『…规程』判为同名"""
    base = _normalize_title_for_dup("钢筋机械连接技术规程")
    assert _normalize_title_for_dup("钢筋机械连接技术规程（2015年版）") == base
    assert _normalize_title_for_dup("钢筋机械连接技术规程(2016年版)") == base


def test_normalize_title_folds_trailing_punctuation():
    """末尾标点差异不算不同名"""
    assert _normalize_title_for_dup("混凝土结构设计规范。") == \
        _normalize_title_for_dup("混凝土结构设计规范")


def test_normalize_title_keeps_real_difference():
    """真不同名不得被判成同名（防过宽）"""
    assert _normalize_title_for_dup("钢筋机械连接规程") != \
        _normalize_title_for_dup("钢筋机械连接技术规程")


def test_normalize_title_handles_empty():
    """空/None 一律归一为空串（title 列可空，不能抛）"""
    assert _normalize_title_for_dup("") == ""
    assert _normalize_title_for_dup(None) == ""


# ═══════════════════════════════════════════
# 判重查询
# ═══════════════════════════════════════════

def test_find_duplicate_by_code_ignores_writing_style(tmp_path, monkeypatch):
    """编号比较走 normalize_spec_code 同口径：无斜杠写法也算同一本"""
    _seed_spec(tmp_path, monkeypatch, "GB/T 50010-2010", "混凝土结构设计规范")
    dups = find_duplicate_specs("GBT50010-2010", "")
    assert [d["code"] for d in dups] == ["GB/T 50010-2010"]
    assert "code" in dups[0]["reasons"]


def test_find_duplicate_by_title_ignores_version_paren(tmp_path, monkeypatch):
    """名称命中：编号写错但名称归一后相同（这正是名称参与判重的唯一价值）"""
    _seed_spec(tmp_path, monkeypatch, "JGJ 107-2016", "钢筋机械连接技术规程")
    dups = find_duplicate_specs("JGJ107-2O16", "钢筋机械连接技术规程（2015年版）")
    assert len(dups) == 1
    assert dups[0]["reasons"] == ["title"]


def test_find_duplicate_ignores_status(tmp_path, monkeypatch):
    """**状态不参与判重**：已废止的旧记录同样算「已存在」"""
    _seed_spec(tmp_path, monkeypatch, "CJJ 2-2008",
               "城市桥梁工程施工与质量验收规范", status="废止")
    dups = find_duplicate_specs("CJJ 2-2008", "")
    assert len(dups) == 1
    assert dups[0]["status"] == "废止", "状态应原样带出供展示"
    assert find_duplicate_specs("", "城市桥梁工程施工与质量验收规范"), \
        "库中只有废止版时，导入同名规范仍应被判重"


def test_find_duplicate_reports_both_reasons(tmp_path, monkeypatch):
    """编号与名称同时命中时两个 reason 都要给出（前端要说清为什么算重）"""
    _seed_spec(tmp_path, monkeypatch, "GB 50010-2010", "混凝土结构设计规范")
    dups = find_duplicate_specs("GB 50010-2010", "混凝土结构设计规范")
    assert len(dups) == 1
    assert set(dups[0]["reasons"]) == {"code", "title"}


def test_find_duplicate_returns_empty_for_blank_input(tmp_path, monkeypatch):
    """两个字段都空 → 不查（避免空串把全库都命中）"""
    _seed_spec(tmp_path, monkeypatch, "GB 50010-2010", "混凝土结构设计规范")
    assert find_duplicate_specs("", "") == []
    assert find_duplicate_specs(None, None) == []


def test_find_duplicate_no_match(tmp_path, monkeypatch):
    _seed_spec(tmp_path, monkeypatch, "GB 50010-2010", "混凝土结构设计规范")
    assert find_duplicate_specs("GB 50204-2015", "混凝土结构工程施工质量验收规范") == []


def test_find_duplicate_does_not_conflate_gb_and_gbt(tmp_path, monkeypatch):
    """归一化只抹写法差异，**不得**把 GB 与 GB/T 混为一谈 —— 两者是不同标准
    （强制性 vs 推荐性）。这是上面「编号同口径比较」的边界：抹掉的分隔符里
    不能连带把斜杠所表达的推荐性语义也抹掉。"""
    _seed_spec(tmp_path, monkeypatch, "GB 50010-2010", "混凝土结构设计规范")
    assert find_duplicate_specs("GB/T 50010-2010", "") == []
    assert find_duplicate_specs("GBT50010-2010", "") == []


def test_find_duplicate_returns_display_fields(tmp_path, monkeypatch):
    """命中项要带出前端展示所需字段（编号/名称/状态/条文数/id）"""
    _seed_spec(tmp_path, monkeypatch, "JGJ 107-2016", "钢筋机械连接技术规程")
    d = find_duplicate_specs("JGJ 107-2016", "")[0]
    assert set(d) >= {"id", "code", "title", "status", "reasons"}


# ═══════════════════════════════════════════
# 接口：/import/check-duplicate（纯读）
# ═══════════════════════════════════════════

def test_check_duplicate_endpoint_hit(auth_client):
    # 库里存带斜杠的写法，查询用无斜杠写法 —— 同一本，须命中
    _insert("GB/T 50010-2010", "混凝土结构设计规范")
    resp = auth_client.post("/import/check-duplicate",
                            json={"code": "GBT50010-2010", "title": ""})
    assert resp.status_code == 200
    dups = resp.json()["duplicates"]
    assert len(dups) == 1 and dups[0]["code"] == "GB/T 50010-2010"


def test_check_duplicate_endpoint_miss_returns_empty_array(auth_client):
    """未命中返回空数组 —— 统一结构，不得是 null/字符串（全局规则 §1.2）"""
    resp = auth_client.post("/import/check-duplicate",
                            json={"code": "GB 50204-2015", "title": "不属于本库的规范"})
    assert resp.status_code == 200
    assert resp.json() == {"duplicates": []}


def test_check_duplicate_endpoint_tolerates_missing_fields(auth_client):
    """缺字段/空 body 不得 500（外部输入必须校验）"""
    assert auth_client.post("/import/check-duplicate", json={}).json() == {"duplicates": []}


# ═══════════════════════════════════════════
# 提交兜底：/import/upload
# ═══════════════════════════════════════════

def test_upload_blocks_duplicate_code_without_confirmation(auth_client):
    """命中同编号且未确认 → 只提示、**不启动管线**、不新增规范"""
    _insert("JGJ 107-2016", "钢筋机械连接技术规程")
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("jgj.md", io.BytesIO(_MD.encode("utf-8")), "text/markdown")},
        data={"code": "JGJ 107-2016", "title": "钢筋机械连接技术规程"},
    )
    assert resp.status_code == 200
    assert "已存在" in resp.text or "重复" in resp.text
    assert 'hx-get="/import/progress/' not in resp.text, "未确认时不得启动导入管线"
    assert 'id="import-dup-blocked"' in resp.text, "须带稳定标记供前端识别（逃生口）"
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM specifications").fetchone()[0] == 1, \
            "未确认时不得新增规范"


def test_upload_proceeds_when_duplicate_confirmed(auth_client, monkeypatch, tmp_path):
    """带 dup_confirmed → 放行（重导是合法需求，不能被硬拒绝）"""
    _patch_pipeline_paths(monkeypatch, tmp_path)
    _insert("JGJ 107-2016", "钢筋机械连接技术规程")
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("jgj.md", io.BytesIO(_MD.encode("utf-8")), "text/markdown")},
        data={"code": "JGJ 107-2016", "title": "钢筋机械连接技术规程",
              "dup_confirmed": "true"},
    )
    assert resp.status_code == 200
    assert 'hx-get="/import/progress/' in resp.text, "已确认应进入导入管线"


def test_upload_file_hash_duplicate_still_wins_over_code_check(auth_client):
    """同一个文件仍走「重复文件」文案（确定性强于编号/名称的疑似重复）"""
    import hashlib
    _insert("GB-DUP", "已存在的规范")
    with get_db() as conn:
        conn.execute("UPDATE specifications SET file_hash = ?",
                     (hashlib.sha256(_MD.encode("utf-8")).hexdigest(),))
    resp = auth_client.post(
        "/import/upload",
        files={"file": ("same.md", io.BytesIO(_MD.encode("utf-8")), "text/markdown")},
        data={"code": "GB 99999", "title": "另一个规范", "dup_confirmed": "true"},
    )
    assert resp.status_code == 200
    assert "该文件已导入过" in resp.text, "同文件应给出确定性的「重复文件」提示"
