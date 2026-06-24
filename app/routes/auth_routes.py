from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_302_FOUND
from app.database import get_db
from app.auth import verify_password, create_access_token
from app.config import ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()

    if user and verify_password(password, user["password_hash"]):
        token = create_access_token({"sub": username})
        resp = RedirectResponse(url="/", status_code=HTTP_302_FOUND)
        resp.set_cookie(
            key="access_token", value=token,
            httponly=True, max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
        return resp

    from app.main import templates
    return templates.TemplateResponse(
        request, "login.html",
        {"error": "用户名或密码错误"},
    )


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=HTTP_302_FOUND)
    resp.delete_cookie("access_token")
    return resp
