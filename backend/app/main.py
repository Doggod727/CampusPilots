from fastapi import FastAPI

from app.core.errors import register_exception_handlers
from app.core.request_id import RequestIdMiddleware
from app.modules.platform.auth_routes import router as auth_router
from app.modules.platform.health import router as health_router
from app.modules.platform.user_routes import router as user_router
from app.modules.platform.rbac_routes import router as rbac_router
from app.modules.platform.sensitive_word_routes import router as sensitive_word_router
from app.modules.platform.moderation_routes import router as moderation_router
from app.modules.platform.audit_routes import router as audit_router


def create_app() -> FastAPI:
    application = FastAPI(
        title="学生生活一站式社区 AI 助手 API",
        version="0.4.0",
    )
    application.add_middleware(RequestIdMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(user_router)
    application.include_router(rbac_router)
    application.include_router(sensitive_word_router)
    application.include_router(moderation_router)
    application.include_router(audit_router)
    return application


app = create_app()
