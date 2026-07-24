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
    """测试 OCR API 连通性"""
    from app.ocr.paddle_api import get_setting

    access_token = get_setting("ocr.access_token")
    if not access_token:
        return JSONResponse(
            {"detail": "请先配置 AI Studio access_token"}, status_code=400
        )

    # 发送最小测试请求
    import httpx
    import base64

    # 生成 1x1 像素的测试图片 (最小 PNG)
    test_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img_b64 = base64.b64encode(test_png).decode("utf-8")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic",
                params={"access_token": access_token},
                data={"image": img_b64},
            )
            data = resp.json()
            if "error_code" in data:
                return JSONResponse(
                    {"detail": f"连接失败: {data.get('error_msg', '未知错误')}"},
                    status_code=400,
                )
            return {"status": "ok", "message": "OCR API 连接正常"}
    except Exception as e:
        logger.error(f"OCR 测试异常: {e}")
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
