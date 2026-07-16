from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.modules.campus_service.work_order_access import WorkOrderScopeRepository
from app.modules.campus_service.work_order_schemas import (
    WorkOrderEventListResponse,
    WorkOrderCreateRequest,
    WorkOrderPageResponse,
    WorkOrderResponse,
    WorkOrderStatus,
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
                scopes=WorkOrderScopeRepository(session),
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


@router.get("", operation_id="listWorkOrders", response_model=WorkOrderPageResponse)
async def list_work_orders(
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("work_order:read"))],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: WorkOrderStatus | None = None,
    campus_code: Annotated[str | None, Query(max_length=30)] = None,
    assigned_to_me: bool = False,
) -> WorkOrderPageResponse:
    data = await service.list_visible(
        actor=actor,
        page=page,
        page_size=page_size,
        status=status,
        campus_code=campus_code,
        assigned_to_me=assigned_to_me,
    )
    return WorkOrderPageResponse(
        data=data, request_id=request.state.request_id, timestamp=datetime.now(UTC)
    )


@router.get(
    "/{work_order_id}", operation_id="getWorkOrder", response_model=WorkOrderResponse
)
async def get_work_order(
    work_order_id: UUID,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("work_order:read"))],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> WorkOrderResponse:
    data = await service.get_visible(actor=actor, work_order_id=work_order_id)
    return WorkOrderResponse(
        data=data, request_id=request.state.request_id, timestamp=datetime.now(UTC)
    )


@router.get(
    "/{work_order_id}/events",
    operation_id="listWorkOrderEvents",
    response_model=WorkOrderEventListResponse,
)
async def list_work_order_events(
    work_order_id: UUID,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("work_order:read"))],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> WorkOrderEventListResponse:
    data = await service.list_events(actor=actor, work_order_id=work_order_id)
    return WorkOrderEventListResponse(
        data=data, request_id=request.state.request_id, timestamp=datetime.now(UTC)
    )
