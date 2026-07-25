from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.campus_service.service_progress import (
    MockCampusSystemAdapter,
    ServiceProgressService,
)
from app.modules.campus_service.service_progress_schemas import (
    ServiceProgressData,
    ServiceProgressQueryRequest,
    ServiceProgressResponse,
)
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.repositories import AuditLogRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/service-progress", tags=["ServiceProgress"])


async def get_service_progress_service(
    actor: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> AsyncIterator[ServiceProgressService]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            adapter = MockCampusSystemAdapter(actor.user_id) if settings.use_mock_campus_adapters else None
            yield ServiceProgressService(
                adapter=adapter,
                session=session,
                audit=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()


@router.post(
    "/queries",
    operation_id="queryExternalServiceProgress",
    response_model=ServiceProgressResponse,
)
async def query_external_service_progress(
    payload: ServiceProgressQueryRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    service: Annotated[ServiceProgressService, Depends(get_service_progress_service)],
) -> ServiceProgressResponse:
    result = await service.query(
        actor_user_id=actor.user_id,
        actor_username=actor.username,
        system_code=payload.system_code,
        business_no=payload.business_no,
        request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=ServiceProgressData.model_validate(result, from_attributes=True),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
