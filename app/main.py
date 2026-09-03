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


def _startup_warmup_embedding():
    """后台线程预热 BGE embedding 模型，消除首次搜索卡顿（不阻塞启动）"""
    import threading

    def _run():
        try:
            from app.ai.embedding import get_model
            m = get_model()
            logger.info("BGE embedding 模型预热完成" if m else "BGE embedding 模型不可用（跳过预热）")
        except Exception as e:
            logger.warning("BGE embedding 预热失败: %s", e)

    threading.Thread(target=_run, daemon=True).start()


def _startup_fts_optimize():
    """后台线程执行 FTS5 optimize（合并碎片），不阻塞应用启动"""
    import threading

    def _run():
        try:
            from app.database import get_db
            with get_db() as conn:
                conn.execute("INSERT INTO clauses_fts(clauses_fts) VALUES('optimize')")
            logger.info("FTS5 optimize 完成")
        except Exception as e:
            logger.warning("FTS5 optimize 失败: %s", e)

    threading.Thread(target=_run, daemon=True).start()


def _startup_log_cleanup():
    """后台线程执行 90 天日志保留清理并记录系统启动（不阻塞启动）

    清理与启动审计放后台线程：init_db() 完成建表后再执行；先清理过期日志再写
    「系统启动」审计，保证启动日志本身不会被过期条件误删（它在清理后写入）。
    """
    import threading

    def _run():
        try:
            from app.database import get_db
            from app.maintenance.log_cleanup import cleanup_expired
            from app.logging_util import log_action
            with get_db() as conn:
                n = cleanup_expired(conn)
            log_action("system", "INFO", "启动日志清理", detail=str(n), username="system")
            log_action("system", "INFO", "系统启动", username="system")
        except Exception as e:
            logger.warning("启动日志清理失败: %s", e)

    threading.Thread(target=_run, daemon=True).start()


def startup():
    init_db()
    _startup_log_cleanup()
    _startup_vector_sync()
    _startup_warmup_embedding()
    _startup_fts_optimize()


app.on_event("startup")(startup)

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

from app.routes.maintenance_routes import router as maintenance_router
app.include_router(maintenance_router)

from app.routes.log_routes import router as log_router
app.include_router(log_router)

from app.routes.param_routes import router as param_router
app.include_router(param_router)


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/welcome.html",
        
    })


@app.get("/health")
async def health():
    return {"status": "ok"}
