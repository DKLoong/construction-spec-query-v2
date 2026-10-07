"""审查页的两个**只读**端点（spec §6.1 / §7，P1 计划 Task 2）。

隔离：按**消费方模块**patch 路径常量（`from app.config import X` 在导入时即绑定，
patch `app.config.*` 无效）。
"""
import pytest

from app.ocr.layout import save_layout
from app.routes.import_routes import create_task

TASK_ID = "a1b2c3d4"


@pytest.fixture
def review_env(auth_client, monkeypatch, tmp_path):
    """隔离环境 + 一个待审查任务；返回 (client, output_dir)。"""
    monkeypatch.setattr("app.database.DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr("app.search.vector_search.LANCE_DB_PATH", str(tmp_path / "lance"))
    monkeypatch.setattr("app.routes.import_routes.UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.routes.import_routes.OUTPUT_DIR", str(tmp_path / "out"))
    for n in ("lance", "up", "out"):
        (tmp_path / n).mkdir(parents=True, exist_ok=True)

    from app.database import init_db
    init_db()
    create_task(TASK_ID, owner="t")
    return auth_client, tmp_path / "out"


def _write_layout(out_dir, **over):
    lay = {
        "version": 1,
        "pages": [{"index": 1, "width": 100, "height": 200}],
        "blocks": [{"label": "text", "content": "正文", "page": 1, "bbox": [1, 2, 3, 4],
                    "order": 1, "global_block_id": 0, "start_line": 0, "end_line": 1,
                    "prefix": "", "suffix": "", "clause_no": "3.0.1"}],
        "base_sha256": "x",
        "stats": {"anchorable": 1, "anchored": 1, "missed": 0},
    }
    lay.update(over)
    d = out_dir / TASK_ID
    d.mkdir(parents=True, exist_ok=True)
    save_layout(lay, d)


# ── /layout ───────────────────────────────────────────

def test_layout_endpoint_returns_blocks_pages_stats(review_env):
    client, out = review_env
    _write_layout(out)

    resp = client.get(f"/import/review/{TASK_ID}/layout")
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocks"][0]["clause_no"] == "3.0.1"
    assert body["blocks"][0]["content"] == "正文"
    assert body["pages"][0]["index"] == 1
    assert body["stats"]["anchorable"] == 1


def test_layout_endpoint_ships_editable_labels(review_env):
    """★ 可编辑 label 白名单必须由**后端下发**（单一来源），前端不得另写一份"""
    from app.ocr.layout import EDITABLE_TEXT_LABELS

    client, out = review_env          # 本文件既有 fixture（隔离四路径 + create_task）
    _write_layout(out)                # 本文件既有播种 helper
    r = client.get(f"/import/review/{TASK_ID}/layout")
    assert r.status_code == 200
    assert r.json()["editable_labels"] == sorted(EDITABLE_TEXT_LABELS)
    assert "text" in r.json()["editable_labels"]


def test_layout_endpoint_ships_the_task_own_not_in_markdown(review_env):
    """★ 「不进 markdown 的 label」名单必须取**该任务真实生效**的那份，不是常量。

    P4 Task 9（D2 裁定）：块页的跨块搜索**覆盖所有「进 md 的块」**，必须把
    「不进 md 的块」（页码 / 页眉页脚 / 图片 / 公式编号…）排除在计数之外 ——
    否则会报出**永远改不动**的匹配。

    而「实际生效」的名单**不是** `DEFAULT_NOT_IN_MARKDOWN`，是
    `DEFAULT_NOT_IN_MARKDOWN | markdownIgnoreLabels`（`classify_labels`），
    且 `markdownIgnoreLabels` 是**用户可编辑**的 OCR 参数
    （`app/ocr/paddle_api.py`：`ocr.{backend}.params` 会**整份替换**默认参数）
    ⇒ 取常量会在「用户给 markdownIgnoreLabels 加了 `table`」这类任务上把**实际不进 md 的块**
    算进匹配。真实并集已随 `anchor_blocks` 的 stats 落进 `layout.json`。

    本用例把 stats 写成一份**与常量不同**的名单 ⇒ 只有「以 stats 为准」才可能通过。
    """
    from app.ocr.layout import DEFAULT_NOT_IN_MARKDOWN

    client, out = review_env
    own = sorted(set(DEFAULT_NOT_IN_MARKDOWN) | {"table", "chart"})   # 模拟用户改过参数
    _write_layout(out, stats={"anchorable": 1, "anchored": 1, "missed": 0,
                              "not_in_markdown": own})
    r = client.get(f"/import/review/{TASK_ID}/layout")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["not_in_markdown"], list), "必须是列表（前端按数组处理）"
    assert body["not_in_markdown"] != sorted(DEFAULT_NOT_IN_MARKDOWN), \
        "断言对象与常量相同 ⇒ 本用例变成了在测回落分支（失去判别力）"
    assert body["not_in_markdown"] == own
    assert "table" in body["not_in_markdown"], "该任务自己的名单被常量覆盖了"


def test_layout_endpoint_falls_back_to_default_not_in_markdown(review_env):
    """回落分支：`stats` 里没有 `not_in_markdown`（或整个 stats 缺失）⇒ 用常量。

    `not_in_markdown` 是**后加**的 stats 键，历史 `layout.json` 不会有它 ⇒ 必须有网兜住，
    否则老任务的审查页会拿到 `None`（前端 `data.not_in_markdown || []` 会退化成
    「什么都不过滤」，把永远改不动的匹配全报出来）。
    """
    from app.ocr.layout import DEFAULT_NOT_IN_MARKDOWN

    client, out = review_env
    # 两种历史形状都要兜住：① stats 在、但没这个键；② stats 整个缺失
    for stats in ({"anchorable": 1, "anchored": 1, "missed": 0}, None):
        _write_layout(out, stats=stats)
        r = client.get(f"/import/review/{TASK_ID}/layout")
        assert r.status_code == 200, f"stats={stats!r} 时端点没兜住：{r.text}"
        assert r.json()["not_in_markdown"] == sorted(DEFAULT_NOT_IN_MARKDOWN), \
            f"stats={stats!r} 时没有回落常量"
    assert "number" in r.json()["not_in_markdown"], "页码块必须在不进 md 的名单里"


def test_layout_endpoint_404_without_layout(review_env):
    """无 layout.json（老任务 / .md / 非扫描件）⇒ 404，前端据此降级（spec §13）。

    ⚠️ **断言 detail 文案**，不只断言状态码：路由**不存在**时 FastAPI 也回 404，
    只查状态码的用例在实现前就是**恒绿空转**。
    """
    client, _ = review_env
    r = client.get(f"/import/review/{TASK_ID}/layout")
    assert r.status_code == 404
    assert r.json()["detail"] == "无版面数据", f"不是本端点的 404：{r.text}"


def test_layout_endpoint_404_for_invalid_task_id(auth_client):
    """task_id 必须是 8 位 hex（防目录拼接越权）"""
    r = auth_client.get("/import/review/ZZZZZZZZ/layout")
    assert r.status_code == 404
    assert r.json()["detail"] == "任务ID非法", f"不是本端点的 404：{r.text}"


# ── /page/{n}.png ─────────────────────────────────────

def test_page_endpoint_rejects_out_of_range_scale(review_env):
    """★ `scale` 是**外部输入**：越界必须挡下（全局规则 §1.1）——
    否则 200 倍缩放能把 PyMuPDF 渲染成一堵墙，把服务端内存吃光。"""
    client, _ = review_env
    for bad in ("0", "0.1", "8.5", "abc", "-1"):
        r = client.get(f"/import/review/{TASK_ID}/page/1.png?scale={bad}")
        assert r.status_code == 422, f"scale={bad} 应被拒"


def test_page_endpoint_404_when_source_missing(review_env):
    """原文件不存在 ⇒ 404，不是 500（同样断言 detail，防与"路由不存在"混淆）"""
    client, _ = review_env
    r = client.get(f"/import/review/{TASK_ID}/page/1.png")
    assert r.status_code == 404
    assert r.json()["detail"] == "原始文件不存在", f"不是本端点的 404：{r.text}"


def test_page_endpoint_renders_png_for_real_pdf(review_env, monkeypatch, tmp_path):
    """★ 真渲染一次：造一个 2 页 PDF 挂到任务上，取第 2 页 ⇒ 200 + PNG 魔数"""
    import fitz

    client, _ = review_env
    pdf = tmp_path / "src.pdf"
    doc = fitz.open()
    for i in (1, 2):
        doc.new_page(width=200, height=300).insert_text((20, 40), f"page {i}")
    doc.save(str(pdf))
    doc.close()

    from app.routes import import_routes as ir
    ir._update_task(TASK_ID, file_path=str(pdf))

    r = client.get(f"/import/review/{TASK_ID}/page/2.png?scale=1.5")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n", "返回的不是 PNG"


def test_page_endpoint_404_for_page_out_of_range(review_env, tmp_path):
    """页号越界 ⇒ 404（不得让 PyMuPDF 抛出来变成 500）"""
    import fitz

    client, _ = review_env
    pdf = tmp_path / "one.pdf"
    doc = fitz.open()
    doc.new_page(width=100, height=100)
    doc.save(str(pdf))
    doc.close()

    from app.routes import import_routes as ir
    ir._update_task(TASK_ID, file_path=str(pdf))

    r = client.get(f"/import/review/{TASK_ID}/page/9.png")
    assert r.status_code == 404
    assert r.json()["detail"] == "页号越界", f"不是本端点的 404：{r.text}"


def test_page_endpoint_404_for_invalid_task_id(auth_client):
    r = auth_client.get("/import/review/ZZZZZZZZ/page/1.png")
    assert r.status_code == 404
    assert r.json()["detail"] == "任务ID非法", f"不是本端点的 404：{r.text}"


def test_page_image_accepts_hidpi_scale(auth_client):
    """★ dpr=2 的屏幕上，适应宽度后请求倍率会到 ~3.0×2=6.0，
    旧上限 3.0 会 422 ⇒ 页图整个加载不出来（比模糊更糟）。"""
    r = auth_client.get("/import/review/deadbeef/page/1.png?scale=6.0")
    assert r.status_code != 422, f"6.0 被判非法：{r.status_code} {r.text[:120]}"
    r2 = auth_client.get("/import/review/deadbeef/page/1.png?scale=9.0")
    assert r2.status_code == 422, "上限没兜住 —— 渲染倍数必须有护栏"
