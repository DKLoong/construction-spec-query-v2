"""compose 端点（计划 Task 3）。

判据来自 spec §10.1（拼装规则单一来源）与 §14（外部输入校验）：
- 不编辑 ⇒ 结果 == `normalize_md(原 md)`，且**去掉规范化后逐字节等于原 md**
- 编辑 ⇒ 只有被编辑的那一段变了
- 外部输入（块下标、值类型、body 大小、JSON 合法性）逐项校验，**断言 detail 文案**
  （只查状态码会空转：路由不存在时 FastAPI 也回 404，本仓踩过）
"""
import json
from pathlib import Path

from app.ocr.layout import anchor_blocks, base_sha256, save_layout
from app.parser.ocr_clean import normalize_md

MD = "3.0.1 第一条。\n\n3.0.2 第二条。\n\n3.0.3 第三条。"


def _blocks_of(md: str) -> list[dict]:
    """三块，**块内容含编号**。

    ⚠️ **必须含编号**：`anchor_blocks` 要求「块内容 == 该行内容」（`_split_affixes` 只剥
    `#` 前缀与 HTML 外壳）。写成 `"第一条。"` 会让**三个块全部锚不上**（实测 `start_line`
    全 None）⇒ 编辑落不到任何区间 ⇒ 编辑类用例变成「看起来在测、实际空转」。
    真实产物正是含编号的：477 个 `text` 块 **477 个锚定成功**，只有内容逐字等于整行才可能。
    """
    raw = [
        {"label": "text", "content": "3.0.1 第一条。", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 0, "global_block_id": 0},
        {"label": "text", "content": "3.0.2 第二条。", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 1, "global_block_id": 1},
        {"label": "text", "content": "3.0.3 第三条。", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 2, "global_block_id": 2},
    ]
    blocks, _ = anchor_blocks(md, raw)
    assert [b["start_line"] for b in blocks] == [0, 2, 4], \
        "前置不成立：三块必须都锚定到 0/2/4 行，否则下面的编辑用例是空转"
    return blocks


def _seed(auth_client, task_id="ab000001", md=MD, sha=None, blocks=None):
    """造一个带 layout.json 的待审查任务，返回 task_id。

    `blocks` 默认按 `md` 锚定；**不需要锚定**的场景（例如只验「规范化有没有接线」）
    显式传 `[]` —— 否则 `_blocks_of` 的前置断言会先炸。
    """
    from tests.conftest import seed_import_task
    from app.routes import import_routes as ir

    seed_import_task(task_id, status="review_needed", progress=50,
                     message="待审查", md_text=md, title="测试", code="GB-X",
                     file_path="/tmp/x.pdf", file_name="x.pdf")
    bl = _blocks_of(md) if blocks is None else blocks
    save_layout({
        "version": 1, "pages": [{"index": 1, "width": 100, "height": 200}],
        "blocks": bl, "base_sha256": sha if sha is not None else base_sha256(md),
        "stats": {},
    }, Path(ir.OUTPUT_DIR) / task_id)
    return task_id


def _post(auth_client, task_id, payload):
    return auth_client.post(f"/import/review/{task_id}/compose",
                            content=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"})


def test_compose_without_edits_keeps_text_byte_exact_before_normalize(auth_client, isolated_paths):
    """不编辑 ⇒ **未编辑区间原样取回**（splice 的结构性保证）：先去规范化再比，逐字节相等

    ⚠️ **本用例**不能**充当「规范化已接线」的证据**：本夹具的 md 既无字面反斜杠 n 也无
    `<table>` ⇒ `normalize_md` 在它上面是**恒等函数** ⇒ 把路由里那句 `normalize_md(...)`
    整句删掉，这行断言照样绿（Task 3 评审实测）。规范化由下一条用例钉住。
    """
    from app.ocr.layout import splice
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["md_text"] == normalize_md(splice(MD, _blocks_of(MD), {}))


def test_compose_applies_storage_normalization(auth_client, isolated_paths):
    """★ 组装结果必须**真的**过一遍 `normalize_md`（spec §8.6 方案 b / §10.4）。

    输入必须挑**规范化确实会改写**的那两类（字面反斜杠 n、裸 `<table>`），否则用例
    在恒等夹具上恒绿，整条落库规范化保证会**静默回归而测试全绿**（Task 3 评审的
    Important 发现，已裁定采纳）。块列表传 `[]`：本用例只验接线，不需要锚定。
    """
    md = "3.0.1 条文" + chr(92) + "n续\n<table><tr><td>甲</td></tr></table>"
    task_id = _seed(auth_client, "ab000003", md=md, blocks=[])
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 200, resp.text
    out = resp.json()["md_text"]
    assert "<br>续" in out, f"字面反斜杠 n 未在组装结果里被规范化：{out!r}"
    assert '<table align="center">' in out, f"表格未在组装结果里被规范化成居中：{out!r}"


def test_compose_applies_only_the_edited_range(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {"1": "改过的第二条。"}})
    assert resp.status_code == 200, resp.text
    out = resp.json()["md_text"]
    assert "改过的第二条。" in out
    assert "3.0.1 第一条。" in out and "3.0.3 第三条。" in out
    # ⚠️ 必须断言**整行**（含编号）已消失：新内容「改过的第二条。」**含**子串「第二条。」，
    #    只查子串会让这条断言恒真
    assert "3.0.2 第二条。" not in out


def test_compose_rejects_out_of_range_index_with_detail(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {"99": "x"}})
    assert resp.status_code == 422
    assert "越界" in resp.json()["detail"]


def test_compose_rejects_non_string_value_with_detail(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = _post(auth_client, task_id, {"edits": {"0": 123}})
    assert resp.status_code == 422
    assert "字符串" in resp.json()["detail"]


def test_compose_rejects_non_json_body_with_detail(auth_client, isolated_paths):
    task_id = _seed(auth_client)
    resp = auth_client.post(f"/import/review/{task_id}/compose", content=b"not json",
                            headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert "JSON" in resp.json()["detail"]


def test_compose_rejects_when_layout_missing(auth_client, isolated_paths):
    from tests.conftest import seed_import_task
    task_id = "ab000002"
    seed_import_task(task_id, status="review_needed", md_text=MD, title="t", code="c",
                     file_path="/tmp/x.pdf", file_name="x.pdf")
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 404
    assert "版面" in resp.json()["detail"]


def test_compose_rejects_stale_layout(auth_client, isolated_paths):
    """★ 指纹守卫：旧 span + 新文本必须 409（否则会把编辑写进错的行区间）"""
    task_id = _seed(auth_client, sha="0" * 64)
    resp = _post(auth_client, task_id, {"edits": {}})
    assert resp.status_code == 409
    assert "不一致" in resp.json()["detail"] or "刷新" in resp.json()["detail"]


def test_compose_rejects_edit_on_block_without_span(auth_client, isolated_paths):
    """★ **无 span 的块不接受编辑**（P2 终审的 Important 发现 I-1）。

    为什么必须拒绝：`splice` 只替换有 span 的块 ⇒ 对无 span 块的编辑会被**静默忽略**
    （200 但毫无效果），而本仓口径是「**静默丢改动比拒绝更坏**」（计划边界 B1）。
    前端 `canEdit` 用 `hasSpan` 已挡住 ⇒ UI 不可达；本用例钉的是**契约层**的兜底，
    防未来前端 bug 或直连调用拿到「成功但无效」。
    """
    task_id = _seed(auth_client, "ab000004", md="3.0.1 第一条。", blocks=[
        {"label": "text", "content": "3.0.1 第一条。", "page": 1, "bbox": [0, 0, 1, 1],
         "order": 0, "global_block_id": 0, "start_line": None, "end_line": None,
         "prefix": "", "suffix": "", "clause_no": None},
    ])
    resp = _post(auth_client, task_id, {"edits": {"0": "改过的内容"}})
    assert resp.status_code == 422, f"无 span 块的编辑应被拒绝，实得 {resp.status_code}"
    assert "未定位" in resp.json()["detail"]
