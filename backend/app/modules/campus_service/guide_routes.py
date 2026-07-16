from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.campus_service.guide_schemas import (
    MaterialChecklistResponse,
    StudentType,
    material_checklist,
)
from app.modules.campus_service.guides import ServiceGuideService
from app.modules.campus_service.material_checklist import MaterialChecklistService
from app.modules.campus_service.repositories import GuideRepository
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/service-guides", tags=["ServiceGuides"])


async def get_guide_service() -> AsyncIterator[ServiceGuideService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield ServiceGuideService(GuideRepository(session))
    finally:
        await database.dispose()


@router.get(
    "/{guide_id}/checklist",
    operation_id="getServiceGuideChecklist",
    response_model=MaterialChecklistResponse,
)
async def get_service_guide_checklist(
    guide_id: UUID,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    service: Annotated[ServiceGuideService, Depends(get_guide_service)],
    campus_code: Annotated[str, Query(max_length=30)],
    student_type: StudentType,
) -> MaterialChecklistResponse:
    detail = await service.get_detail(
        guide_id,
        campus_code=campus_code,
        student_type=student_type,
    )
    checklist = MaterialChecklistService().build_checklist(
        detail,
        campus_code=campus_code,
        student_type=student_type,
    )
    return SuccessResponse(
        data=material_checklist(checklist),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
