from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.modules.campus_service.work_order_schemas import (
    WorkOrderCreateRequest,
    WorkOrderResponse,
)
from app.modules.campus_service.work_orders import (
    CreateWorkOrderCommand,
    WorkOrderService,
)
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.repositories import AuditLogRepository, IdempotencyRecordRepository

router = APIRouter(prefix="/api/v1/work-orders", tags=["WorkOrders"])


async def get_work_order_service() -> AsyncIterator[WorkOrderService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield WorkOrderService(
                session=session,
                campuses=CampusReferenceRepository(session),
                work_orders=WorkOrderRepository(session),
                events=WorkOrderEventRepository(session),
                idempotency=IdempotencyService(
                    session=session,
                    repository=IdempotencyRecordRepository(session),
                ),
                audit=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()


@router.post(
    "",
    operation_id="createWorkOrder",
    status_code=201,
    response_model=WorkOrderResponse,
)
async def create_work_order(
    payload: WorkOrderCreateRequest,
    request: Request,
    actor: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("work_order:create")),
    ],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
) -> JSONResponse:
    result = await service.create(
        actor=actor,
        command=CreateWorkOrderCommand(**payload.model_dump()),
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )
    return JSONResponse(
        result.body,
        status_code=result.status_code,
        headers={REQUEST_ID_HEADER: result.request_id},
    )
