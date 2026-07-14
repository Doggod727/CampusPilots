from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.moderation_schemas import (
    ModerationCaseData,
    ModerationCasePageData,
    ModerationCasePageResponse,
    ModerationCaseResponse,
    ModerationSort,
    ModerationStatus,
    RiskLevel,
    TargetModule,
    moderation_case_data,
)
from app.modules.platform.repositories import ModerationCaseRepository
from app.core.request_id import REQUEST_ID_HEADER
from app.modules.platform.moderation_decision import (
    ModerationCaseNotFound,
    ModerationDecisionResult,
    ModerationDecisionService,
    moderation_decision_service_context,
)
from pydantic import BaseModel, ConfigDict, Field
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/moderation/cases", tags=["Moderation"])


class ModerationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected", "escalated"]
    reason: str = Field(min_length=2, max_length=500)
    version: int = Field(ge=1)


async def get_repository() -> AsyncIterator[ModerationCaseRepository]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield ModerationCaseRepository(session)
    finally:
        await database.dispose()


async def get_decision_service() -> AsyncIterator[ModerationDecisionService]:
    async with moderation_decision_service_context(get_settings()) as service:
        yield service


@router.get("", operation_id="listModerationCases", response_model=ModerationCasePageResponse)
async def list_moderation_cases(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("moderation:read"))],
    repository: Annotated[ModerationCaseRepository, Depends(get_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: ModerationStatus | None = None,
    risk_level: RiskLevel | None = None,
    target_module: TargetModule | None = None,
    sort: ModerationSort = "-created_at",
) -> ModerationCasePageResponse:
    items, total = await repository.list_page(
        page=page, page_size=page_size, status=status, risk_level=risk_level,
        target_module=target_module, sort=sort,
    )
    return SuccessResponse(
        data=ModerationCasePageData(
            items=[moderation_case_data(item) for item in items],
            pagination={
                "page": page, "page_size": page_size, "total": total,
                "total_pages": ceil(total / page_size) if total else 0,
            },
        ), request_id=request.state.request_id, timestamp=datetime.now(UTC),
    )


@router.get("/{case_id}", operation_id="getModerationCase", response_model=ModerationCaseResponse)
async def get_moderation_case(
    case_id: UUID,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("moderation:read"))],
    repository: Annotated[ModerationCaseRepository, Depends(get_repository)],
) -> ModerationCaseResponse:
    case = await repository.get_by_id(case_id)
    if case is None:
        raise ModerationCaseNotFound()
    return SuccessResponse(
        data=moderation_case_data(case), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post("/{case_id}/decision", operation_id="decideModerationCase", response_model=ModerationCaseResponse)
async def decide_moderation_case(
    case_id: UUID,
    payload: ModerationDecisionRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("moderation:decide"))],
    service: Annotated[ModerationDecisionService, Depends(get_decision_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> JSONResponse:
    result = await service.decide(
        actor=actor, case_id=case_id, decision=payload.decision,
        reason=payload.reason, expected_version=payload.version,
        idempotency_key=idempotency_key, request_id=request.state.request_id,
        request_body=payload.model_dump(mode="json"),
    )
    return JSONResponse(
        status_code=result.status_code, content=result.body,
        headers={REQUEST_ID_HEADER: result.request_id},
    )
