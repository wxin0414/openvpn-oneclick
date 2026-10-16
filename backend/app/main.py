import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import crud
from app.api.api_v1 import api_router
from app.api.deps import get_db_context
from app.core.config import get_settings, insecure_settings
from app.core.logging_config import setup_logging
from app.db import base  # noqa: F401
from app.db.session import engine
from app.services import jobs, status_sync

settings = get_settings()
logger = logging.getLogger(__name__)

# create_all 只会建新表，不会给「已存在」的表补索引，因此这里显式补齐。
_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_auditlog_created_at ON auditlog (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_auditlog_action_created_at ON auditlog (action, created_at)",
)


def _ensure_indexes() -> None:
    """为已有数据库补建审计日志索引（幂等）。"""
    try:
        with engine.begin() as conn:
            for statement in _INDEX_STATEMENTS:
                conn.execute(text(statement))
    except Exception:  # noqa: BLE001 - 索引补建失败不应阻止启动
        logger.warning("补建审计日志索引失败（面板仍可运行，但日志查询会变慢）", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/停止后台线程。

    * 在线状态同步过去发生在 GET /clients 里（把分页读接口变成写事务），
      现在改由后台线程按固定间隔执行。
    * 证书签发/吊销这类秒级 EasyRSA 操作改由后台任务执行，启动时一并收尾。
    """
    status_sync.start_background_sync()
    try:
        yield
    finally:
        status_sync.stop_background_sync()
        jobs.registry.shutdown()


def create_app() -> FastAPI:
    is_dev = settings.deploy_env.upper() == "DEV"

    # Logging setup (console + rotating file)
    setup_logging()
    app = FastAPI(
        title=settings.app_name,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json" if is_dev else None,
        docs_url="/docs" if is_dev else None,
        redoc_url="/redoc" if is_dev else None,
        lifespan=lifespan,
    )

    cors_origins = ["*"] if is_dev else settings.cors_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False if is_dev else True,
        allow_methods=["*"],
        allow_headers=["*"],
        # 显式设置预检缓存时间（秒）。生产建议同源部署（nginx 反代 /api），
        # 那样根本不会产生 OPTIONS 预检。
        max_age=settings.cors_max_age,
    )

    base.Base.metadata.create_all(bind=engine)
    _ensure_indexes()

    # seed default admin if not exists
    with get_db_context() as db:
        admin = crud.user.get_by_username(db, username=settings.default_admin_username)
        if not admin:
            from app.schemas.user import UserCreate

            crud.user.create(
                db,
                obj_in=UserCreate(
                    username=settings.default_admin_username,
                    email=None,
                    password=settings.default_admin_password,
                    is_active=True,
                    is_superuser=True,
                ),
            )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # 启动自检：把仍然存在的公开默认值明确喊出来，避免"以为改了其实没改"
    for problem in insecure_settings(settings):
        logger.warning("[安全自检] %s", problem)

    if is_dev:
        logger.info("Swagger UI: http://localhost:8000/docs  |  OpenAPI: %s/openapi.json", settings.api_v1_prefix)

    return app


app = create_app()
