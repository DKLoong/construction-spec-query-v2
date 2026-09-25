"""规范分类保存后的标签同步（T5）

用户反馈：改完规范分类，下方虽然有「✅ 分类已保存」，但**表格里的分类标签没变**
——保存响应只写回了表单区（#spec-class-area），没人管表格那一行的分类单元格。

改法：分类单元格抽成 partials/spec_class_cell.html（列表与 OOB 共用一份定义），
保存响应在表单片段之外再带一个 hx-swap-oob 的单元格。表单本身走 hx-put，
htmx 会处理 OOB，无需额外 JS。
"""
import re
from pathlib import Path

from app.database import get_db, init_db
from tests.test_spec_routes import _setup_spec_data

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def _setup(monkeypatch, tmp_path, name):
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / name))
    init_db()
    with get_db() as conn:
        spec_id = _setup_spec_data(conn)
    return spec_id


def test_specs_table_class_cell_has_id_for_oob(auth_client, monkeypatch, tmp_path):
    """分类单元格须带 id —— 它就是保存响应里 OOB 的落点"""
    spec_id = _setup(monkeypatch, tmp_path, "scs1.db")
    html = auth_client.get("/specs/list").text
    assert f'id="spec-class-cell-{spec_id}"' in html, \
        "分类单元格没有 id，保存后无法定位并更新它（标签不同步的根因）"


def test_update_class_response_carries_oob_cell_with_new_labels(auth_client, monkeypatch, tmp_path):
    """保存分类的响应须带 OOB 单元格，且内容是新分类标签"""
    spec_id = _setup(monkeypatch, tmp_path, "scs2.db")
    resp = auth_client.put(f"/specs/{spec_id}/class",
                           data={"dim2_stage": "施工", "dim3_usage": "民用建筑"})
    assert resp.status_code == 200

    assert f'id="spec-class-cell-{spec_id}"' in resp.text, "响应缺少分类单元格的 OOB 片段"
    assert 'hx-swap-oob="true"' in resp.text, "分类单元格未标记为 OOB，htmx 不会替换它"

    # OOB 片段里必须是**新**标签（抽出来单独断言，避免被表单区的旧值蒙混）
    start = resp.text.index(f'id="spec-class-cell-{spec_id}"')
    cell = resp.text[max(0, start - 100):start + 400]
    assert "施工" in cell and "民用建筑" in cell, f"OOB 单元格未带新标签：{cell[:200]}"


def test_update_class_response_splits_into_cell_and_form(auth_client, monkeypatch, tmp_path):
    """两个片段各司其职：表单区（#spec-class-area 落点）与单元格（OOB）都存在"""
    spec_id = _setup(monkeypatch, tmp_path, "scs3.db")
    resp = auth_client.put(f"/specs/{spec_id}/class",
                           data={"dim2_stage": "", "dim3_usage": ""})
    assert resp.status_code == 200
    assert "分类已保存" in resp.text, "表单区仍应给出保存成功提示"
    assert f'id="spec-class-cell-{spec_id}"' in resp.text


def test_clearing_class_renders_dash_in_oob_cell(auth_client, monkeypatch, tmp_path):
    """分类被清空时 OOB 单元格显示占位「-」而不是空白（与列表渲染一致）"""
    spec_id = _setup(monkeypatch, tmp_path, "scs4.db")
    resp = auth_client.put(f"/specs/{spec_id}/class",
                           data={"dim2_stage": "", "dim3_usage": ""})
    start = resp.text.index(f'id="spec-class-cell-{spec_id}"')
    cell = resp.text[start:start + 400]
    assert ">" in cell
    assert "-" in cell, f"清空分类后单元格应显示占位符：{cell[:200]}"


def test_oob_carrier_is_not_a_table_cell():
    """OOB 载体不得是 <td>/<tr> 等「脱离表格上下文即被解析器丢弃」的元素

    这条断言来自一次真实翻车：OOB 最初挂在 <td> 上，响应文本完全正确、本文件
    上面的用例全绿，但页面上标签纹丝不动。原因是 htmx 用 **DOMParser** 解析响应
    来提取 OOB 元素，而 HTML 解析器在 body 上下文中会把顶层 <td> 直接丢弃：
        DOMParser().parseFromString('<td>x</td>', 'text/html').body.children.length === 0
        DOMParser().parseFromString('<div>ok</div><td>x</td>', ...) → 只剩 [DIV]
    故载体改为 <span>（行内元素，任何上下文都保留），落点挂在单元格内部。
    详见 partials/spec_class_edit.html 的注释。
    """
    html = (TEMPLATES / "partials" / "spec_class_edit.html").read_text(encoding="utf-8")
    m = re.search(r"<([a-zA-Z]+)[^>]*\shx-swap-oob=", html)
    assert m, "未找到 OOB 载体"
    tag = m.group(1).lower()
    assert tag not in ("td", "tr", "th", "tbody", "thead", "tfoot", "col", "colgroup"), \
        f"OOB 载体是 <{tag}>：脱离表格上下文会被 HTML 解析器丢弃，OOB 永远不会生效"
