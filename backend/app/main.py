import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.errors import register_exception_handlers
from app.core.request_id import RequestIdMiddleware
from app.modules.platform.auth_routes import router as auth_router
from app.modules.platform.health import router as health_router
from app.modules.platform.readiness import router as readiness_router
from app.modules.platform.user_routes import router as user_router
from app.modules.platform.rbac_routes import router as rbac_router
from app.modules.platform.sensitive_word_routes import router as sensitive_word_router
from app.modules.platform.moderation_routes import router as moderation_router
from app.modules.platform.audit_routes import router as audit_router
from app.modules.platform.config_routes import router as config_router
from app.modules.platform.dashboard_routes import router as dashboard_router
from app.modules.agent_platform.catalog_routes import router as agent_catalog_router
from app.modules.agent_platform.run_routes import router as agent_run_router
from app.modules.agent_platform.dataset_routes import router as dataset_router
from app.modules.agent_platform.training import router as training_router
from app.modules.agent_platform.model_registry import router as model_router
from app.modules.agent_platform.evaluation_routes import router as evaluation_router
from app.modules.agent_platform.internal_tools import router as internal_tool_router


def create_app() -> FastAPI:
    application = FastAPI(
        title="学生生活一站式社区 AI 助手 API",
        version="0.4.0",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").rstrip("/")
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-Id",
            "Idempotency-Key",
        ],
    )
    # Add Request-Id after CORS so preflight responses receive the same
    # correlation header as normal and error responses.
    application.add_middleware(RequestIdMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(readiness_router)
    application.include_router(auth_router)
    application.include_router(user_router)
    application.include_router(rbac_router)
    application.include_router(sensitive_word_router)
    application.include_router(moderation_router)
    application.include_router(audit_router)
    application.include_router(config_router)
    application.include_router(dashboard_router)
    application.include_router(agent_catalog_router)
    application.include_router(agent_run_router)
    application.include_router(dataset_router)
    application.include_router(training_router)
    application.include_router(model_router)
    application.include_router(evaluation_router)
    application.include_router(internal_tool_router)
    return application


app = create_app()
