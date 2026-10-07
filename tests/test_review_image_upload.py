"""审查页图片落盘端点 `POST /import/review/{task_id}/image`（P4 图片线的**共用落点**）。

为什么需要它：OCR 对**表格单元格里的图形**不产出任何东西（实测：某 122 页文档的表1，
48 个 `<td>` 里图例格全空、该表 md 里 0 个 `<img>`；把 `maxPixels` 顶到官方上限重跑结果逐项相同）
⇒ 只能人工补图，而「补」需要把新图**落进** `outputs/{task_id}/imgs/`。

为什么是「收字节」这一个端点：图片三入口（点击选文件 / 拖拽 / 粘贴）拿到的是**任意位图**、
没有裁切参数可传 ⇒ 它们必然汇到一个收字节的落点；拖框取图那条先 `GET /crop` 拿 PNG 再回传。
若再为拖框单开一个「服务端裁切+落盘」端点，就出现**两个落点**，
spec §8.4「三者汇到同一落点」当场作废，且文件名/类型/大小/magic 那套校验要写两遍。

隔离：按**消费方模块** patch 路径常量（`from app.config import X` 在导入时即绑定，
patch `app.config.*` 无效）。与 `test_review_layout_routes.py` 同范式。
"""
import struct
import zlib

import pytest

import app.routes.import_routes as ir
from app.routes.import_routes import _copy_ocr_images, create_task

TASK_ID = "a1b2c3d4"
MISSING_ID = "ffffffff"          # 合法 8 位十六进制，但库里没有
URL = f"/import/review/{TASK_ID}/image"


def _png(w=1, h=1):
    """**真正合法**的 PNG（PIL 可解）。

    ⚠️ 不能用「看着像 PNG 的字节」凑：magic 校验的正例若本身不是合法 PNG，
    取反后的负例（声明 png 却给别的格式）就失去了判别力，用例会以「实现拒了」的样子红。
    """
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


@pytest.fixture
def env(auth_client, monkeypatch, tmp_path):
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


def _post(client, body, ctype="image/png", task=TASK_ID):
    return client.post(f"/import/review/{task}/image", content=body,
                       headers={"Content-Type": ctype})


# ── 正常路径 ───────────────────────────────────────────────

def test_uploads_png_and_returns_reference(env):
    """合法 PNG → 200 + filename/url；磁盘字节**与上传字节逐字节相同**"""
    client, out = env
    body = _png(3, 2)
    resp = _post(client, body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) >= {"filename", "url"}, data
    assert data["url"] == f"/import/review/{TASK_ID}/imgs/{data['filename']}"
    saved = out / TASK_ID / "imgs" / data["filename"]
    assert saved.is_file(), "端点回了 200 但文件不在 imgs/ 下"
    assert saved.read_bytes() == body, "落盘字节与上传字节不一致"


def test_second_upload_never_overwrites_the_first(env):
    """★ **不覆盖**（spec §8.4 落盘策略）：两次上传得到两个不同文件名，且第一个文件**字节不变**。

    只断言「两次都 200」会放过覆盖：那就等于用户的图被后一张悄悄替换掉。"""
    client, out = env
    a, b = _png(1, 1), _png(4, 3)
    fa = _post(client, a).json()["filename"]
    fb = _post(client, b).json()["filename"]
    assert fa != fb, f"两次上传拿到同一个文件名（会互相覆盖）：{fa}"
    d = out / TASK_ID / "imgs"
    assert (d / fa).read_bytes() == a, "第二次上传改写了第一个文件"
    assert (d / fb).read_bytes() == b


def test_filename_has_no_path_separators(env):
    """文件名由**服务端**生成 ⇒ 不得含路径段（防穿越的最后一道）"""
    client, _ = env
    name = _post(client, _png()).json()["filename"]
    assert "/" not in name and "\\" not in name and ".." not in name, name


def test_unique_name_rerolls_on_collision(env, monkeypatch):
    """★ 撞名必须**换名**而不是覆盖：预置一个同名文件，函数要摇出另一个名字。

    用受控的 `uuid4` 序列把「撞一次再摇中」这个分支逼出来 —— 否则这条分支在真实
    随机下几乎不可达，等于没有网。"""
    _, out = env
    imgs = out / TASK_ID / "imgs"
    imgs.mkdir(parents=True, exist_ok=True)

    class _FakeUuid:
        def __init__(self, h):
            self.hex = h

    seq = iter(["deadbeefdead", "cafebabecafe"])
    monkeypatch.setattr(ir.uuid, "uuid4", lambda: _FakeUuid(next(seq)))
    (imgs / "up_deadbeefdead.png").write_bytes(b"x")

    assert ir._unique_image_name(imgs, ".png") == "up_cafebabecafe.png"


# ── 外部输入校验（全局规则 §1.1）────────────────────────────

def test_rejects_bad_task_id(env):
    """task_id 非 8 位十六进制 → 404（防目录拼接越权）。

    ⚠️ 两类要**分开**断言：含 `../` 的那种根本进不到处理器（HTTP 层先把路径规范化了，
    回的是 FastAPI 默认的 `{"detail":"Not Found"}`）。把它与「处理器自己拒的」混在一起
    断言 detail 文案，会得到一条**与被测实现无关**的红 —— 实测踩过。
    """
    client, _ = env
    # (a) 路径穿越：在路由之前就被规范化 ⇒ 只要求 404，文案不归本端点管
    assert _post(client, _png(), task="../etc").status_code == 404
    # (b) 能路由进来、但格式非法：由处理器拒绝，文案归本端点
    for bad in ("zzzzzzzz", "abc", "a1b2c3d4e5"):
        r = _post(client, _png(), task=bad)
        assert r.status_code == 404, (bad, r.status_code)
        assert r.json()["detail"] == "任务ID非法", (bad, r.json())


def test_rejects_unknown_task(env):
    """合法格式但库里没有该任务 → 404（避免往悬空目录写文件）"""
    client, _ = env
    r = _post(client, _png(), task=MISSING_ID)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "任务不存在或已过期", r.json()


def test_rejects_non_image_content_type(env):
    client, _ = env
    r = _post(client, _png(), ctype="text/plain")
    assert r.status_code == 422, r.status_code
    assert r.json()["detail"] == "仅支持 png/jpg/webp 图片", r.json()


def test_rejects_empty_body(env):
    client, _ = env
    assert _post(client, b"").status_code == 422


def test_rejects_oversize_body(env, monkeypatch):
    """超限 → 413。把上限 patch 到「比 body 少 1 字节」，而不是真发 10MB
    （真发既慢又占内存），也不写死一个数（PNG 高度可压缩，写死的阈值会随图内容漂移）。"""
    client, _ = env
    body = _png()
    monkeypatch.setattr(ir, "_IMAGE_MAX_BYTES", len(body) - 1)
    r = _post(client, body)
    assert r.status_code == 413, r.status_code


def test_rejects_magic_mismatch(env):
    """★ 声明是 png、body 却是别的格式 → 422。

    这条是「类型白名单」之外的**第二道**：只信 Content-Type 的话，
    任何文件都能被改个后缀存进来，之后被 `<img>` 引用时才发现是坏的。"""
    client, _ = env
    gif = b"GIF89a" + b"\x00" * 32
    r = _post(client, gif, ctype="image/png")
    assert r.status_code == 422, r.status_code
    assert r.json()["detail"] == "图片内容与声明的格式不符", r.json()


def test_write_failure_returns_500(env, monkeypatch):
    """写盘失败 → 500（把原子写抽成模块级函数，便于在此注入失败）"""
    client, _ = env

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ir, "_write_image_atomic", _boom)
    r = _post(client, _png())
    assert r.status_code == 500, r.status_code
    assert r.json()["detail"] == "图片保存失败", r.json()


# ── 与导入链路的衔接 ───────────────────────────────────────

def test_new_image_is_carried_into_the_long_term_dir(env, tmp_path):
    """★ 落盘的新图会被 `_copy_ocr_images` 整目录带走 ⇒ 导入后条文页不会 404。

    这条是「落盘目录选对了」的**唯一**证明：若落到别处，本用例红而其余全绿。"""
    client, out = env
    name = _post(client, _png(2, 2)).json()["filename"]
    dest = tmp_path / "long_term"
    dest.mkdir()
    _copy_ocr_images(out / TASK_ID, dest)
    copied = dest / "imgs" / name
    assert copied.is_file(), "新图没被带进长期目录（导入后引用会 404）"
    assert copied.read_bytes() == (out / TASK_ID / "imgs" / name).read_bytes()
