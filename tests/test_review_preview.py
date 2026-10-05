"""preview 端点（计划 Task 4）。

它的价值（spec §11.4）不是「显示条文」而是「**你这次编辑改了切分吗**」——
所以判据必须落在「改一条编号行 ⇒ 条数与 clause_no 序列随之变化」上，
而不是「返回了个列表」。
"""
import json

from tests.test_review_compose import MD, _seed


def _post(auth_client, task_id, payload):
    return auth_client.post(f"/import/review/{task_id}/preview",
                            content=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"})


def test_preview_returns_clause_list_of_current_md(auth_client, isolated_paths):
    task_id = _seed(auth_client, "ab000010")
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [c["clause_no"] for c in data["clauses"]] == ["3.0.1", "3.0.2", "3.0.3"]
    assert data["count"] == 3


def test_preview_mirrors_phase2_cover_filter(auth_client, isolated_paths):
    """★ 封面/出版信息页伪条文必须**同样被丢掉**。

    漏掉 `_filter_cover_clauses` 会让预览数**系统性地高于**入库数，而 spec §18 的
    P2 出口判据正是「切分预览与导入后实际条文数一致」。
    （`## 第X页` 是 OCR 管线的分隔标记，parse_markdown 会为它生成一条伪条文
     —— 见 `app/parser/ocr_clean.py` 的模块 docstring。）
    """
    from app.parser.md_parser import parse_markdown
    from app.routes.import_routes import _filter_cover_clauses

    md = ("## 第1页\nICS 77.140.60\n中华人民共和国国家标准\n代替 GB/T 1499.1—2008\n\n"
          "3.0.1 第一条。")
    assert len(parse_markdown(md)) == 2, "前置不成立：夹具没有产出封面伪条文"
    assert len(_filter_cover_clauses(parse_markdown(md))) == 1, "前置不成立：封面未被过滤"

    task_id = _seed(auth_client, "ab000014", md=md, blocks=[])
    # ⚠️ `blocks=[]` 不是可选项：本用例的 md 是**另一份**文本，默认的 `_blocks_of(md)`
    # 会因前置断言（三块须锚到 0/2/4）先炸。本用例只关心条数，不需要锚定。
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 1, "预览没有镜像 Phase 2 的封面过滤"


def test_preview_reflects_an_edit_that_adds_a_clause(auth_client, isolated_paths):
    """★ 补写一条**带编号的新行** ⇒ 预览必须多出一条（这正是用户补 OCR 丢失内容的场景）

    ⚠️ 编辑值**必须带上原行整行内容**（含 `3.0.3`）：行级编辑器里那一行就是整行，
    用户是在其**后**追加一行；只写「第三条。」会把 `3.0.3` 这个编号整行抹掉，
    切出来变成 `3.0.1/3.0.2/3.0.4`（少一条），用例会红得莫名其妙。实测已核。
    """
    task_id = _seed(auth_client, "ab000011")
    edited = "3.0.3 第三条。\n3.0.4 补写的新条文。"
    resp = _post(auth_client, task_id, {"edits": {"2": edited}})
    assert resp.status_code == 200, resp.text
    nos = [c["clause_no"] for c in resp.json()["clauses"]]
    assert nos == ["3.0.1", "3.0.2", "3.0.3", "3.0.4"], f"新增条文未被切出：{nos}"


def test_preview_rejects_bad_edit_with_same_detail_as_compose(auth_client, isolated_paths):
    """与 compose 共用前置 ⇒ 错误文案必须一致（两条端点不能各说各话）"""
    task_id = _seed(auth_client, "ab000012")
    resp = _post(auth_client, task_id, {"edits": {"99": "x"}})
    assert resp.status_code == 422
    assert "越界" in resp.json()["detail"]


def test_preview_404_without_layout(auth_client, isolated_paths):
    from tests.conftest import seed_import_task
    task_id = "ab000013"
    seed_import_task(task_id, status="review_needed", md_text=MD, title="t", code="c",
                     file_path="/tmp/x.pdf", file_name="x.pdf")
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 404
    assert "版面" in resp.json()["detail"]
