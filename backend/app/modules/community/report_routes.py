from collections.abc import AsyncIterator
from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.community.reports import ReportService
from app.modules.community.repositories import ReportRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import SensitiveWordScanner
from app.modules.platform.repositories import (
    AuditLogRepository, IdempotencyRecordRepository, ModerationCaseRepository,
    SensitiveWordRepository,
)
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/reports", tags=["CommunityInteractions"])


class TargetType(str, Enum):
    post = "post"; comment = "comment"; event = "event"; lost_found = "lost_found"


class ReasonCode(str, Enum):
    spam = "spam"; abuse = "abuse"; privacy = "privacy"; fraud = "fraud"
    unsafe = "unsafe"; other = "other"


class ContentReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: TargetType
    target_id: UUID
    reason_code: ReasonCode
    details: str = Field(min_length=2, max_length=500)


class ContentReportDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    target_type: TargetType
    target_id: UUID
    reason_code: ReasonCode
    status: str
    moderation_case_id: UUID | None = None
    created_at: datetime


ContentReportResponse = SuccessResponse[ContentReportDataModel]


async def get_report_service() -> AsyncIterator[ReportService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            audit = AuditService(AuditLogRepository(session))
            yield ReportService(
                session=session, repository=ReportRepository(session),
                moderation=ModerationService(
                    session=session, scanner=SensitiveWordScanner(SensitiveWordRepository(session)),
                    repository=ModerationCaseRepository(session), audit_service=audit,
                ), idempotency=IdempotencyService(
                    session=session, repository=IdempotencyRecordRepository(session),
                ), audit=audit,
            )
    finally:
        await database.dispose()


@router.post("", operation_id="createContentReport", status_code=201,
             response_model=ContentReportResponse)
async def create_report(
    payload: ContentReportRequest, request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("community:write"))],
    service: Annotated[ReportService, Depends(get_report_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    result = await service.submit(
        actor=actor, idempotency_key=idempotency_key,
        request_id=request.state.request_id, request_body=body,
        **payload.model_dump(mode="python"),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)
