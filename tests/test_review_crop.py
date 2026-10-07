"""`GET /import/review/{task_id}/crop/{page_no}.png` 的回归网（**补零覆盖**）。

这个端点此前**没有任何测试**，而它是「拖框取图」与「图片块按 bbox 显示」的公共出口
⇒ 它的几何一旦偏了，两处都会静默出错（框选看着对、裁出来的不是那块）。

⚠️ 断言必须**逐条核 detail 文案**：只查状态码的话，一个**不存在的路由**同样回 404，
用例会以「全绿」的样子空转（本仓已有同类教训，见 `test_review_layout_routes.py` 里的
`不是本端点的 404` 写法）。
"""
import struct

import pytest

from app.ocr.layout import save_layout
from app.routes.import_routes import create_task

TASK_ID = "a1b2c3d4"
PW, PH = 200, 300          # 页栅格尺寸；让 PDF 页点尺寸与之相等 ⇒ base = 1.0，换算无小数残差


@pytest.fixture
def env(auth_client, monkeypatch, tmp_path):
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


def _png_size(data: bytes) -> tuple[int, int]:
    """从 IHDR 读像素尺寸（不引入 PIL 依赖）"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "返回的不是 PNG"
    return struct.unpack(">II", data[16:24])


def _ready(env, tmp_path, *, pages=None, pdf=True):
    """造好「有 layout 尺寸 + 有真实 PDF」的任务"""
    client, out = env
    save_layout({"version": 1,
                 "pages": pages if pages is not None else [{"index": 1, "width": PW, "height": PH}],
                 "blocks": [], "base_sha256": "x", "stats": {}}, out / TASK_ID)
    if pdf:
        import fitz
        doc = fitz.open()
        doc.new_page(width=PW, height=PH).insert_text((20, 40), "x")
        src = tmp_path / "src.pdf"
        doc.save(str(src))
        doc.close()
        from app.routes import import_routes as ir
        ir._update_task(TASK_ID, file_path=str(src))
    return client


def _crop(client, q, task=TASK_ID, page=1):
    return client.get(f"/import/review/{task}/crop/{page}.png?{q}")


# ── 外部输入校验 ───────────────────────────────────────────

def test_rejects_invalid_task_id(auth_client):
    r = _crop(auth_client, "x=1&y=1&w=10&h=10", task="ZZZZZZZZ")
    assert r.status_code == 404
    assert r.json()["detail"] == "任务ID非法", f"不是本端点的 404：{r.text}"


def test_rejects_scale_out_of_range(env, tmp_path):
    client = _ready(env, tmp_path)
    for bad in ("0.1", "5.0"):
        r = _crop(client, f"x=1&y=1&w=10&h=10&scale={bad}")
        assert r.status_code == 422, (bad, r.status_code)
        assert r.json()["detail"] == "scale 需在 [0.25, 4.0]", (bad, r.json())


def test_rejects_non_finite_params(env, tmp_path):
    """NaN / inf 必须在进门处挡住，否则会带着它们去算裁切矩形"""
    client = _ready(env, tmp_path)
    for q in ("x=nan&y=1&w=10&h=10", "y=inf&x=1&w=10&h=10", "w=-inf&x=1&y=1&h=10"):
        r = _crop(client, q)
        assert r.status_code == 422, (q, r.status_code)
        assert r.json()["detail"] == "裁切参数非法", (q, r.json())


def test_rejects_non_positive_size(env, tmp_path):
    client = _ready(env, tmp_path)
    for q in ("x=1&y=1&w=0&h=10", "x=1&y=1&w=10&h=-5"):
        r = _crop(client, q)
        assert r.status_code == 422, (q, r.status_code)
        assert r.json()["detail"] == "裁切宽高必须为正", (q, r.json())


def test_404_when_page_has_no_layout_dims(env, tmp_path):
    """layout 里该页没有 width/height ⇒ 404（不是 500，也不能拿 0 去除）"""
    client = _ready(env, tmp_path, pages=[{"index": 1}])
    r = _crop(client, "x=1&y=1&w=10&h=10")
    assert r.status_code == 404
    assert r.json()["detail"] == "该页没有版面尺寸", f"不是本端点的 404：{r.text}"


def test_404_when_source_file_missing(env, tmp_path):
    client = _ready(env, tmp_path, pdf=False)
    r = _crop(client, "x=1&y=1&w=10&h=10")
    assert r.status_code == 404
    assert r.json()["detail"] == "原始文件不存在", f"不是本端点的 404：{r.text}"


def test_422_when_fully_out_of_page(env, tmp_path):
    """整块都在页外 ⇒ 夹取后为零 ⇒ 422（回一张 0×0 的图比拒绝更坏）"""
    client = _ready(env, tmp_path)
    r = _crop(client, f"x={PW + 50}&y={PH + 50}&w=10&h=10")
    assert r.status_code == 422
    assert r.json()["detail"] == "裁切区域为空", r.json()


# ── 几何（把「框选 → 栅格 → 裁出的图」钉死）────────────────

def test_crop_output_size_is_request_times_scale(env, tmp_path):
    """★ 输出像素 = **请求的栅格区域 × scale**。

    这条是「端点真按给的矩形裁」的唯一证明 —— 只断言 200 + PNG 魔数的话，
    一个「无论传什么都裁左上角」的实现照样全绿（本仓已有同类教训）。"""
    client = _ready(env, tmp_path)
    r = _crop(client, "x=10&y=20&w=50&h=30&scale=2.0")
    assert r.status_code == 200, r.text
    w, h = _png_size(r.content)
    assert abs(w - 100) <= 4 and abs(h - 60) <= 4, f"输出 {w}×{h}，应为 50×30 × 2.0 = 100×60"


def test_partially_out_of_page_is_clamped(env, tmp_path):
    """部分出页 ⇒ 按**夹取后**的区域裁，而不是照单全收或直接拒"""
    client = _ready(env, tmp_path)
    # 请求 x 从 180 起、宽 50 ⇒ 夹到 [180, 200] = 宽 20；y 同理夹到 20
    r = _crop(client, f"x=180&y={PH - 20}&w=50&h=50&scale=1.0")
    assert r.status_code == 200, r.text
    w, h = _png_size(r.content)
    assert abs(w - 20) <= 4 and abs(h - 20) <= 4, f"输出 {w}×{h}，应为夹取后的 20×20"


def test_two_rects_do_not_share_a_cache_file(env, tmp_path):
    """★ 缓存文件名编码了坐标，两个不同矩形不得互相串号。

    串号的症状极隐蔽：第二张图会**静默显示成第一张**（都是合法 PNG、尺寸也对不上预期）。"""
    client = _ready(env, tmp_path)
    a = _crop(client, "x=0&y=0&w=40&h=20&scale=1.0")
    b = _crop(client, "x=0&y=0&w=80&h=60&scale=1.0")
    assert a.status_code == 200 and b.status_code == 200
    wa, ha = _png_size(a.content)
    wb, hb = _png_size(b.content)
    assert abs(wa - 40) <= 4 and abs(ha - 20) <= 4, (wa, ha)
    assert abs(wb - 80) <= 4 and abs(hb - 60) <= 4, (wb, hb)
