from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.agent_platform.evaluations import (
    EvaluationComparisonData,
    EvaluationCreateCommand,
    EvaluationData,
    EvaluationPageData,
    EvaluationRepository,
    EvaluationService,
)
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.repositories import AuditLogRepository, IdempotencyRecordRepository
from app.shared.responses import SuccessResponse


class EvaluationCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_ids: tuple[UUID, ...] = Field(min_length=2, max_length=5)

    @field_validator("evaluation_ids")
    @classmethod
    def unique_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evaluation_ids must be unique")
        return value


EvaluationResponse = SuccessResponse[EvaluationData]
EvaluationListResponse = SuccessResponse[EvaluationPageData]
EvaluationCompareResponse = SuccessResponse[EvaluationComparisonData]

router = APIRouter(prefix="/api/v1/evaluations", tags=["Evaluations"])


@asynccontextmanager
async def evaluation_context(settings: Settings):
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield EvaluationService(
                session,
                EvaluationRepository(session),
                IdempotencyService(session=session, repository=IdempotencyRecordRepository(session)),
                AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()


async def get_evaluation_service() -> AsyncIterator[EvaluationService]:
    async with evaluation_context(get_settings()) as service:
        yield service


def replay_response(result: tuple[int, dict, str]) -> JSONResponse:
    status, body, request_id = result
    return JSONResponse(body, status_code=status, headers={REQUEST_ID_HEADER: request_id})


@router.get("", operation_id="listEvaluations", response_model=EvaluationListResponse)
async def list_evaluations(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("evaluation:read"))],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationListResponse:
    return SuccessResponse(
        data=await service.list(page, page_size),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post("", operation_id="createEvaluation", status_code=202, response_model=EvaluationResponse)
async def create_evaluation(
    payload: EvaluationCreateCommand,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("evaluation:run"))],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    return replay_response(await service.create(actor, payload, idempotency_key, request.state.request_id))


@router.post("/compare", operation_id="compareEvaluations", response_model=EvaluationCompareResponse)
async def compare_evaluations(
    payload: EvaluationCompareRequest,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("evaluation:read"))],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationCompareResponse:
    return SuccessResponse(
        data=await service.compare(payload.evaluation_ids),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.get("/{evaluation_id}", operation_id="getEvaluation", response_model=EvaluationResponse)
async def get_evaluation(
    evaluation_id: UUID,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("evaluation:read"))],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationResponse:
    return SuccessResponse(
        data=await service.detail(evaluation_id),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
