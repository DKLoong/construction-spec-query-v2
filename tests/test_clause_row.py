"""条文行一致性（T3）：正常行只有一份定义 + 「编辑」一律走分栏编辑页

**为什么需要这个测试**：同一个「正常行」曾有三份拷贝——条文列表的循环体、
分类编辑的 saved 分支、行内编辑的 saved 分支。后两份的「编辑」按钮没跟着升级到
分栏编辑页，于是「点分类 → 取消 → 点编辑」会掉回旧的行内输入框（用户 2026-09-25
报的 bug，其猜测是「旧代码没清理干净」，实为三份拷贝各自进化）。

三份拷贝靠人眼同步必然再次漂移，故本 Task 收敛为 `partials/clause_row.html` 一份，
并用下面的「三处渲染逐字相同」把它锁死：将来谁改了其中一处、忘了另一处，这里就红。
"""
import re
from pathlib import Path

from tests.test_spec_routes import _setup_spec_data

APP = Path(__file__).resolve().parent.parent / "app"
ROW_RE = re.compile(r'<tr id="clause-row-(\d+)".*?</tr>', re.S)


def _row(html: str, clause_id: int) -> str:
    """从整段 HTML 里取出指定条文的行（含空白原样，便于逐字比较）"""
    m = re.search(rf'<tr id="clause-row-{clause_id}".*?</tr>', html, re.S)
    assert m, f"未找到条文 {clause_id} 的行"
    return m.group(0)


def _setup(monkeypatch, tmp_path, name):
    from app.database import get_db, init_db
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / name))
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
        clause_id = conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ? ORDER BY id LIMIT 1", (spec_id,)
        ).fetchone()["id"]
    return spec_id, clause_id


# ═══════════════════════════════════════════
# 三处渲染必须逐字相同（防再次分叉）
# ═══════════════════════════════════════════

def test_normal_row_identical_in_list_and_class_cancel(auth_client, monkeypatch, tmp_path):
    """条文列表里的行 与 分类编辑取消后回填的行 必须逐字相同"""
    spec_id, clause_id = _setup(monkeypatch, tmp_path, "cr1.db")

    listed = _row(auth_client.get(f"/specs/{spec_id}/clauses").text, clause_id)
    restored = _row(auth_client.get(f"/specs/{spec_id}/clauses/{clause_id}/row-class").text,
                    clause_id)

    assert listed == restored, (
        "条文列表的行与分类取消回填的行不一致——两者必须来自同一份 partial，"
        "否则按钮/列结构会再次各自漂移")


def test_normal_row_identical_after_class_save(auth_client, monkeypatch, tmp_path):
    """分类保存后回填的行 也与列表行逐字相同（原值提交，只为比对渲染）"""
    spec_id, clause_id = _setup(monkeypatch, tmp_path, "cr2.db")

    listed = _row(auth_client.get(f"/specs/{spec_id}/clauses").text, clause_id)
    saved = auth_client.put(f"/specs/{spec_id}/clauses/{clause_id}/class",
                            data={"dim4_specialty": "", "dim5_location": "",
                                  "dim6_material": ""})
    assert saved.status_code == 200
    assert _row(saved.text, clause_id) == listed


# ═══════════════════════════════════════════
# 「编辑」按钮一律走分栏编辑页（本次 bug 的判据）
# ═══════════════════════════════════════════

def test_row_edit_button_opens_split_editor(auth_client, monkeypatch, tmp_path):
    """回填行的「编辑」必须走 editClause()（分栏编辑页），不得回退到行内表单端点"""
    spec_id, clause_id = _setup(monkeypatch, tmp_path, "cr3.db")
    row = _row(auth_client.get(f"/specs/{spec_id}/clauses/{clause_id}/row-class").text, clause_id)

    assert f"editClause({spec_id}, {clause_id})" in row, \
        "「编辑」按钮未指向分栏编辑页——这正是用户报的「掉回旧编辑界面」缺陷"
    assert '/edit"' not in row, "行内编辑端点 /edit 不应再出现在条文行里"


def test_legacy_inline_edit_endpoint_removed(auth_client, monkeypatch, tmp_path):
    """旧的行内编辑端点已删除（其唯一的入口就是上面的按钮）"""
    spec_id, clause_id = _setup(monkeypatch, tmp_path, "cr4.db")
    resp = auth_client.get(f"/specs/{spec_id}/clauses/{clause_id}/edit")
    assert resp.status_code == 404


def test_no_source_references_legacy_edit_endpoint():
    """全仓模板与静态脚本不得再引用旧行内编辑端点（删干净，不留半截入口）"""
    offenders = []
    for base in (APP / "templates", APP.parent / "static"):
        for path in base.rglob("*"):
            if path.suffix not in (".html", ".js"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'/clauses/\{\{[^}]*\}\}/edit"', text):
                offenders.append(str(path.relative_to(APP.parent)))
    assert not offenders, f"仍引用旧行内编辑端点：{offenders}"
