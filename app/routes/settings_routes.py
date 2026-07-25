"""设置管理路由"""
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/settings")
async def get_settings(request: Request):
    """获取所有设置"""
    from app.database import get_db

    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.put("/settings")
async def save_settings(request: Request):
    """批量保存设置"""
    from app.database import get_db

    body = await request.json()
    with get_db() as conn:
        for key, value in body.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (str(key), str(value)),
            )
    return {"status": "ok"}


@router.post("/settings/test-ocr")
async def test_ocr(request: Request):
    """测试 OCR API 连通性 — 支持多后端"""
    import base64
    import struct
    import zlib

    # 解析请求体
    access_token = ""
    backend = ""
    try:
        body = await request.json()
        access_token = body.get("access_token", "").strip()
        backend = body.get("backend", "").strip()
    except Exception:
        pass

    # 回退到数据库读取
    if not access_token:
        from app.ocr.paddle_api import get_setting
        access_token = get_setting("ocr.access_token")
    if not backend:
        from app.ocr.paddle_api import get_setting
        backend = get_setting("ocr.backend") or "paddle-vl"

    if not access_token:
        return JSONResponse(
            {"detail": "请先配置 access_token"}, status_code=400
        )

    # 生成测试图片
    def _make_test_png_b64() -> str:
        w, h = 15, 15
        raw = b""
        for y in range(h):
            raw += b"\x00"  # filter: none
            for x in range(w):
                if 3 <= y <= 11 and 2 <= x <= 12:
                    raw += b"\x00\x00\x00\xff"  # 黑色
                else:
                    raw += b"\xff\xff\xff\xff"  # 白色
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)

        def _chunk(ctype, data):
            c = ctype + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

        png = b"\x89PNG\r\n\x1a\n"
        png += _chunk(b"IHDR", ihdr)
        png += _chunk(b"IDAT", zlib.compress(raw))
        png += _chunk(b"IEND", b"")
        return base64.b64encode(png).decode()

    img_b64 = _make_test_png_b64()

    import httpx

    try:
        if backend == "paddle-vl":
            # 测试 PaddleOCR-VL：提交一个最小的 task 提交请求
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser/task",
                    params={"access_token": access_token},
                    data={"file_data": img_b64, "file_name": "test.png"},
                )
                data = resp.json()
                if "error_code" in data:
                    return JSONResponse(
                        {"detail": f"连接失败: {data.get('error_msg', '未知错误')}"},
                        status_code=400,
                    )
                task_id = data.get("result", {}).get("task_id", "")
                if task_id:
                    return {"status": "ok", "message": "PaddleOCR-VL API 连接正常"}
                return JSONResponse(
                    {"detail": "PaddleOCR-VL 响应异常：未返回 task_id"}, status_code=400,
                )

        elif backend == "accurate-basic":
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic",
                    params={"access_token": access_token},
                    data={"image": img_b64},
                )
                data = resp.json()
                if "error_code" in data:
                    return JSONResponse(
                        {"detail": f"连接失败: {data.get('error_msg', '未知错误')}"},
                        status_code=400,
                    )
                return {"status": "ok", "message": "accurate_basic API 连接正常"}

        elif backend == "custom":
            from app.ocr.paddle_api import get_setting
            base_url = get_setting("ocr.custom.base_url")
            if not base_url:
                return JSONResponse(
                    {"detail": "请先配置自定义 OCR 的 Base URL"}, status_code=400,
                )
            model = get_setting("ocr.custom.model") or "gpt-4o"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 5,
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code == 200:
                    return {"status": "ok", "message": "自定义 OCR API 连接正常"}
                else:
                    try:
                        err_data = resp.json()
                        err_msg = err_data.get("error", {}).get("message", f"HTTP {resp.status_code}")
                    except Exception:
                        err_msg = f"HTTP {resp.status_code}"
                    return JSONResponse(
                        {"detail": f"连接失败: {err_msg}"}, status_code=400,
                    )

        else:
            return JSONResponse(
                {"detail": f"未知的 OCR 后端: {backend}"}, status_code=400,
            )

    except Exception as e:
        logger.error(f"OCR 测试异常 (backend={backend}): {e}")
        return JSONResponse(
            {"detail": "OCR API 连接失败，请检查网络连接或稍后重试"}, status_code=400
        )


@router.post("/settings/test-ai")
async def test_ai(request: Request):
    """测试 AI API 连通性（传入临时后端参数）"""
    body = await request.json()
    backend_name = body.get("backend", "")
    api_key = body.get("api_key", "")
    base_url = body.get("base_url", "")
    model = body.get("model", "")

    # CLI 后端无需测试 API 连通性
    if backend_name in ("claude", "codex"):
        return JSONResponse(
            {"detail": "CLI 后端 (claude/codex) 通过本地命令行运行，无需测试 API 连通性"},
            status_code=400,
        )

    if not api_key:
        return JSONResponse(
            {"detail": "请先填写 API Key"}, status_code=400
        )

    if backend_name == "custom":
        if not base_url:
            return JSONResponse(
                {"detail": "请填写自定义 base_url"}, status_code=400
            )
    else:
        from app.ai.provider_presets import PROVIDERS
        preset = PROVIDERS.get(backend_name)
        if preset:
            base_url = preset["base_url"]
            model = preset["default_model"]

    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 5,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                return {"status": "ok", "message": "AI API 连接正常"}
            else:
                data = resp.json()
                error_msg = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
                return JSONResponse(
                    {"detail": f"连接失败: {error_msg}"}, status_code=400
                )
    except Exception as e:
        logger.error(f"AI 测试异常: {e}")
        return JSONResponse(
            {"detail": "AI API 连接失败，请检查网络连接或稍后重试"}, status_code=400
        )
