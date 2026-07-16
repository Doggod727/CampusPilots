from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.campus_service.electricity import ElectricityService
from app.modules.campus_service.electricity_http import ElectricityHttpService
from app.modules.campus_service.electricity_schemas import (
    ElectricityBalanceResponse,
    ElectricityTopupRequestData,
    ElectricityTopupResponse,
    electricity_balance_data,
)
from app.modules.campus_service.repositories import ElectricityRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.repositories import AuditLogRepository, IdempotencyRecordRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/electricity", tags=["Electricity"])


async def get_electricity_http_service() -> AsyncIterator[ElectricityHttpService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            repository = ElectricityRepository(session)
            yield ElectricityHttpService(
                session=session,
                repository=repository,
                electricity=ElectricityService(repository),
                idempotency=IdempotencyService(
                    session=session,
                    repository=IdempotencyRecordRepository(session),
                ),
                audit=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()


@router.get(
    "/accounts/{room_id}",
    operation_id="getElectricityBalance",
    response_model=ElectricityBalanceResponse,
)
async def get_electricity_balance(
    room_id: UUID,
    request: Request,
    actor: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("electricity:read_own")),
    ],
    service: Annotated[ElectricityHttpService, Depends(get_electricity_http_service)],
) -> ElectricityBalanceResponse:
    result = await service.get_balance(actor=actor, room_id=room_id)
    return SuccessResponse(
        data=electricity_balance_data(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/topup-requests",
    operation_id="createElectricityTopupRequest",
    status_code=201,
    response_model=ElectricityTopupResponse,
)
async def create_electricity_topup_request(
    payload: ElectricityTopupRequestData,
    request: Request,
    actor: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("electricity:topup_request:create")),
    ],
    service: Annotated[ElectricityHttpService, Depends(get_electricity_http_service)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
) -> JSONResponse:
    result = await service.create_topup(
        actor=actor,
        room_id=payload.room_id,
        amount=payload.amount_cny,
        approval_id=payload.approval_id,
        agent_run_id=payload.agent_run_id,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    return JSONResponse(
        result.body,
        status_code=result.status_code,
        headers={REQUEST_ID_HEADER: result.request_id},
    )
