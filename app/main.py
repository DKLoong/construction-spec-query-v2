import logging
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import BASE_DIR
from app.auth import decode_access_token
from app.database import init_db
from jose import JWTError

logger = logging.getLogger(__name__)

app = FastAPI(title="鏂藉伐瑙勮寖鏌ヨ绯荤粺 V2")

# 搴旂敤鍚姩鏃跺垵濮嬪寲鏁版嵁搴?@app.on_event("startup")
def _startup_vector_sync():
    """后台线程执行向量索引自愈，不阻塞应用启动

    对比 LanceDB 向量表与数据库现存条文，清理孤儿向量（历史遗留/删除未同步），
    防止旧数据污染语义检索候选池。
    """
    import threading

    def _run():
        try:
            from app.search.vector_search import VectorStore
            removed = VectorStore().sync_with_db()
            if removed:
                logger.info("启动向量自愈：清理孤儿向量 %d 条", removed)
        except Exception as e:
            logger.warning("启动向量自愈失败: %s", e)

    threading.Thread(target=_run, daemon=True).start()


def startup():
    init_db()
    _startup_vector_sync()

# 静态文件
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Jinja2 模板
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public_paths = ["/login", "/static", "/health"]
        if any(request.url.path.startswith(p) for p in public_paths):
            return await call_next(request)

        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/login", status_code=302)

        try:
            payload = decode_access_token(token)
            request.state.username = payload.get("sub")
        except JWTError:
            return RedirectResponse(url="/login", status_code=302)

        return await call_next(request)


app.add_middleware(AuthMiddleware)

from app.routes.auth_routes import router as auth_router
app.include_router(auth_router)

from app.routes.import_routes import router as import_router
app.include_router(import_router)

from app.routes.rules_routes import router as rules_router
app.include_router(rules_router)

from app.routes.spec_routes import router as spec_router
app.include_router(spec_router)

from app.routes.search_routes import router as search_router
app.include_router(search_router)

from app.routes.qa_routes import router as qa_router
app.include_router(qa_router)

from app.routes.settings_routes import router as settings_router
app.include_router(settings_router)

from app.routes.synonym_routes import router as synonym_router
app.include_router(synonym_router)


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/welcome.html",
        
    })


@app.get("/health")
async def health():
    return {"status": "ok"}
