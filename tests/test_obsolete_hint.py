"""废止提示（T2）：替代关系落库方向 + 条文详情页的三级兜底文案

**背景（本 Task 的核心）**：`replaced_by_code` 一个字段三种理解——
prompt 写「被替代的规范编号（废止/修订中时若有）」、导入界面标签写「被替代编号」、
下游 `import_routes` 却拿它去标记**库中该编号**的规范为废止（= 当成「我替代掉的旧规范」）。
前两者读作「替代我的新规范」，第三者读作「我替代的旧规范」，**方向相反**。

后果：导入一本废止规范、且替代它的新版已在库中时，新版会被误标为废止并反向关联。
故本 Task 不假设 AI 的填法，改为**按本规范状态推断方向**（见 `_link_replacement`），
两种 AI 输出都能落到正确方向。
"""
import pytest

from app.database import get_db, init_db
from app.routes.import_routes import _link_replacement


def _mk_spec(conn, code, title, status="现行", **extra):
    cols = ["code", "title", "status", *extra.keys()]
    vals = [code, title, status, *extra.values()]
    conn.execute(
        f"INSERT INTO specifications ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        vals,
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _spec_row(conn, spec_id):
    return dict(conn.execute("SELECT * FROM specifications WHERE id = ?", (spec_id,)).fetchone())


# ═══════════════════════════════════════════
# schema
# ═══════════════════════════════════════════

@pytest.mark.usefixtures("qa_db")
def test_new_db_has_replaced_by_code_column():
    """新建库即含 replaced_by_code 列（不能只靠迁移补，新库也要有）"""
    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(specifications)")}
    assert "replaced_by_code" in cols


@pytest.mark.usefixtures("qa_db")
def test_migration_adds_column_to_legacy_db():
    """旧库（无该列）经 init_db 迁移后补上——新库有列不等于老库能补"""
    with get_db() as conn:
        conn.execute("ALTER TABLE specifications DROP COLUMN replaced_by_code")
        assert "replaced_by_code" not in {
            r[1] for r in conn.execute("PRAGMA table_info(specifications)")}
    init_db()   # 迁移应把列补回（旧库升级路径）
    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(specifications)")}
    assert "replaced_by_code" in cols


# ═══════════════════════════════════════════
# 方向推断：废止/修订中 → 编号是「替代它的新规范」
# ═══════════════════════════════════════════

@pytest.mark.usefixtures("qa_db")
def test_obsolete_spec_links_itself_to_existing_new_spec():
    """废止规范：编号命中库中新规范时，关联方向是「自己 → 新规范」"""
    with get_db() as conn:
        new_id = _mk_spec(conn, "GB 50010-2024", "混凝土结构设计规范（2024）")
        old_id = _mk_spec(conn, "GB 50010-2010", "混凝土结构设计规范", status="废止")
        _link_replacement(conn, old_id, "GB 50010-2024", "废止")

        old = _spec_row(conn, old_id)
        assert old["replace_by_spec_id"] == new_id, "废止规范应指向替代它的新规范"
        assert old["replaced_by_code"] == "GB 50010-2024", "替代者编号应留存供详情页显示"


@pytest.mark.usefixtures("qa_db")
def test_obsolete_spec_never_marks_new_spec_obsolete():
    """**回归护栏**：废止规范不得把库中的新规范标为废止（方向反了就出这个事故）"""
    with get_db() as conn:
        new_id = _mk_spec(conn, "GB 50010-2024", "混凝土结构设计规范（2024）")
        old_id = _mk_spec(conn, "GB 50010-2010", "混凝土结构设计规范", status="废止")
        _link_replacement(conn, old_id, "GB 50010-2024", "废止")

        new = _spec_row(conn, new_id)
        assert new["status"] == "现行", f"新规范被误标为 {new['status']}——方向颠倒了"
        assert new["replace_by_spec_id"] is None, "新规范不该被反向关联到旧规范"


@pytest.mark.usefixtures("qa_db")
def test_obsolete_spec_keeps_code_when_new_spec_absent():
    """废止规范：新版尚未入库时仍留存编号（详情页据此给出编号）"""
    with get_db() as conn:
        old_id = _mk_spec(conn, "JGJ 107-2010", "钢筋机械连接技术规程", status="废止")
        _link_replacement(conn, old_id, "JGJ 107-2024", "废止")

        old = _spec_row(conn, old_id)
        assert old["replaced_by_code"] == "JGJ 107-2024"
        assert old["replace_by_spec_id"] is None, "新版不在库中，不该凭空造出关联"


@pytest.mark.usefixtures("qa_db")
def test_revising_spec_follows_obsolete_branch():
    """修订中的规范同属「被替代」一侧，走同一分支"""
    with get_db() as conn:
        new_id = _mk_spec(conn, "GB 50204-2025", "混凝土结构工程施工质量验收规范")
        old_id = _mk_spec(conn, "GB 50204-2015", "混凝土结构工程施工质量验收规范", status="修订中")
        _link_replacement(conn, old_id, "GB 50204-2025", "修订中")

        assert _spec_row(conn, old_id)["replace_by_spec_id"] == new_id
        assert _spec_row(conn, new_id)["status"] == "现行"


# ═══════════════════════════════════════════
# 方向推断：现行 → 编号是「它替代掉的旧规范」（既有行为保持不变）
# ═══════════════════════════════════════════

@pytest.mark.usefixtures("qa_db")
def test_current_spec_still_marks_old_spec_obsolete():
    """现行规范：编号命中库中旧规范时，旧规范被标废止并反向关联（原行为不得回退）"""
    with get_db() as conn:
        old_id = _mk_spec(conn, "GB 50010-2010", "混凝土结构设计规范", status="现行")
        new_id = _mk_spec(conn, "GB 50010-2024", "混凝土结构设计规范（2024）")
        _link_replacement(conn, new_id, "GB 50010-2010", "现行")

        old = _spec_row(conn, old_id)
        assert old["status"] == "废止"
        assert old["replace_by_spec_id"] == new_id
        # 现行规范自己不写 replaced_by_code（它没有「被替代」的含义）
        assert _spec_row(conn, new_id)["replaced_by_code"] is None


@pytest.mark.usefixtures("qa_db")
def test_empty_code_is_noop():
    """空编号不产生任何写入（不许把空串当匹配目标）"""
    with get_db() as conn:
        sid = _mk_spec(conn, "GB 50010-2010", "混凝土结构设计规范", status="废止")
        _link_replacement(conn, sid, "", "废止")
        row = _spec_row(conn, sid)
        assert row["replaced_by_code"] is None
        assert row["replace_by_spec_id"] is None


@pytest.mark.usefixtures("qa_db")
def test_self_reference_is_ignored():
    """同码重导：编号指向自己时不得自我关联/自我标废"""
    with get_db() as conn:
        sid = _mk_spec(conn, "GB 50010-2010", "混凝土结构设计规范", status="废止")
        _link_replacement(conn, sid, "GB 50010-2010", "废止")
        row = _spec_row(conn, sid)
        assert row["replace_by_spec_id"] is None, "不得自引用"
        assert row["status"] == "废止", "状态不应被自己改动"


# ═══════════════════════════════════════════
# 条文详情页文案（三级兜底）
# ═══════════════════════════════════════════

def _detail_html(auth_client, monkeypatch, tmp_path, dbname, spec_kwargs):
    """建一本规范 + 一条条文，返回条文详情片段 HTML"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / dbname))
    init_db()
    with get_db() as conn:
        spec_id = _mk_spec(conn, spec_kwargs.pop("code"), spec_kwargs.pop("title"),
                           spec_kwargs.pop("status", "现行"), **spec_kwargs)
        conn.execute(
            "INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?,?,?,?)",
            (spec_id, "1.0.1", "测试条文", "测试内容"),
        )
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    resp = auth_client.get(f"/clause/{clause_id}")
    assert resp.status_code == 200
    return resp.text


def test_detail_shows_new_spec_code_and_title(auth_client, monkeypatch, tmp_path):
    """库中有新版：提示带编号 + 名称（用户要求的最终形态）"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "oh1.db"))
    init_db()
    with get_db() as conn:
        _mk_spec(conn, "GB 50010-2024", "混凝土结构设计规范（2024）")
        old_id = _mk_spec(conn, "GB 50010-2010", "混凝土结构设计规范", status="废止")
        _link_replacement(conn, old_id, "GB 50010-2024", "废止")
        conn.execute("INSERT INTO clauses (spec_id, clause_no, title, content) VALUES (?,?,?,?)",
                     (old_id, "1.0.1", "测试条文", "测试内容"))
        clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    html = auth_client.get(f"/clause/{clause_id}").text
    assert "本规范已废止，请查阅新版规范：GB 50010-2024 混凝土结构设计规范（2024）" in html


def test_detail_shows_code_only_when_new_spec_absent(auth_client, monkeypatch, tmp_path):
    """新版未入库：提示退化到「只有编号」，不编造名称"""
    html = _detail_html(auth_client, monkeypatch, tmp_path, "oh2.db", {
        "code": "JGJ 107-2010", "title": "钢筋机械连接技术规程", "status": "废止",
        "replaced_by_code": "JGJ 107-2024",
    })
    assert "本规范已废止，请查阅新版规范：JGJ 107-2024" in html


def test_detail_plain_hint_without_any_info(auth_client, monkeypatch, tmp_path):
    """无任何替代信息：保持原样文案（不得出现悬空的冒号）"""
    html = _detail_html(auth_client, monkeypatch, tmp_path, "oh3.db", {
        "code": "JGJ 107-2010", "title": "钢筋机械连接技术规程", "status": "废止",
    })
    assert "本规范已废止，请查阅新版规范" in html
    assert "请查阅新版规范：" not in html, "没有编号时不该出现冒号"


def test_detail_hint_absent_for_current_spec(auth_client, monkeypatch, tmp_path):
    """现行规范且无替代关系：不显示废止提示"""
    html = _detail_html(auth_client, monkeypatch, tmp_path, "oh4.db", {
        "code": "GB 50204-2015", "title": "混凝土结构工程施工质量验收规范",
    })
    assert "本规范已废止" not in html
