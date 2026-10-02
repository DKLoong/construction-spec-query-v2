import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import BASE_DIR
from app.auth import decode_access_token
from app.database import init_db
from app.logging_setup import setup_logging
from jose import JWTError

logger = logging.getLogger(__name__)

app = FastAPI(title="规范智能检索与问答系统")

# 应用启动时初始化数据库
@app.on_event("startup")
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


def _startup_import_sweeper():
    """后台常驻线程：周期性回收超期的导入任务台账（import_tasks 表）

    台账原先**无 TTL**：任务进入 done/error 后条目永久驻留（只有用户主动取消才删），
    长期运行只增不减；落库（import_tasks）之后这条依然成立——表同样只增不减。
    停在 review_needed 的条目还额外绑着磁盘上的原文件与 OCR 产物（单份可达 68MB）。
    时限与口径见 import_routes.sweep_progress_store，两档时限均可在参数设置页调整。
    """
    import threading

    def _run():
        from app.routes.import_routes import sweep_progress_store, IMPORT_SWEEP_INTERVAL_S
        while True:
            try:
                res = sweep_progress_store()
                if res["rows"]:
                    logger.info("导入任务台账回收：%d 行、磁盘 %d 份",
                                res["rows"], res["disk"])
            except Exception as e:
                # 单轮失败不能终止线程（否则之后再也不清理）
                logger.warning("导入任务台账回收失败: %s", e)
            time.sleep(IMPORT_SWEEP_INTERVAL_S)

    threading.Thread(target=_run, daemon=True).start()


def startup():
    setup_logging()  # 先接日志桥，后续启动步骤的告警才能进 system_logs
    init_db()
    # 自愈必须**同步**执行、且排在 init_db() 之后、开始接受请求之前：
    # 异步会让请求读到仍标 processing 的僵尸任务
    from app.routes.import_routes import heal_interrupted_tasks
    try:
        healed = heal_interrupted_tasks()
    except Exception as e:
        # 自愈失败不得阻止应用启动：它紧跟 init_db()，表必然已存在，
        # 异常只可能来自真正的 DB 故障——而那种情况 init_db() 已经先抛了
        logger.warning("启动自愈失败（不阻止启动）: %s", e)
    else:
        if healed:
            logger.warning("启动自愈：%d 个导入任务因服务重启被中断，已标记为需重跑", healed)
    _startup_log_cleanup()
    _startup_import_sweeper()
    _startup_vector_sync()
    _startup_warmup_embedding()
    _startup_fts_optimize()


app.on_event("startup")(startup)

# 静态文件
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Jinja2 模板
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def split_values(value) -> list[str]:
    """把「半角逗号分隔的多值分类」拆成列表（**显示层专用**）。

    口径必须与另外两处一致（否则又是"同一值三种理解"）：
      - 分类树 `import_routes._build_tree_nodes` 显式按 `,` 拆成多个节点；
      - 检索 `sql_search` 用 `col LIKE '%值%'` 子串命中（点任一子值都能筛到）。
    分类输入框的提示也写明分隔符是**半角**逗号：
    `data-hint='如需设置多个分类，请用半角标点(英文标点)","隔开。'` ⇒ 中文逗号不拆。

    之前只有**显示层**没拆：`施工,验收` 被整串塞进一个 `dim-tag`，看起来是一个标签
    （2026-09-29 修）。空值/None → 空列表；每项 strip、空项丢弃。

    ⚠️ 只改显示、**不改数据**：写库的值保持原样逗号串（检索与分类树都依赖它）。
    """
    if not value:
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


templates.env.filters["split_values"] = split_values


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

from app.routes.lexicon_routes import router as lexicon_router
app.include_router(lexicon_router)

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
