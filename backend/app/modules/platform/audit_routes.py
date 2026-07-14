from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import get_settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit_schemas import (
    AuditLogData,
    AuditLogPageData,
    AuditLogPageResponse,
    AuditLogResponse,
    audit_log_data,
)
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.repositories import AuditLogRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/audit-logs", tags=["Audit"])


class AuditLogNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="AUDIT_LOG_NOT_FOUND", message="审计日志不存在")


async def get_repository() -> AsyncIterator[AuditLogRepository]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield AuditLogRepository(session)
    finally:
        await database.dispose()


@router.get("", operation_id="listAuditLogs", response_model=AuditLogPageResponse)
async def list_audit_logs(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("audit:read"))],
    repository: Annotated[AuditLogRepository, Depends(get_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    actor_user_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    resource_type: Annotated[str | None, Query(max_length=100)] = None,
    request_id: Annotated[str | None, Query(max_length=64)] = None,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
) -> AuditLogPageResponse:
    items, total = await repository.list_page(
        page=page, page_size=page_size, actor_user_id=actor_user_id,
        action=action, resource_type=resource_type, request_id=request_id,
        from_time=from_time, to_time=to_time,
    )
    return SuccessResponse(
        data=AuditLogPageData(
            items=[audit_log_data(item) for item in items],
            pagination={
                "page": page, "page_size": page_size, "total": total,
                "total_pages": ceil(total / page_size) if total else 0,
            },
        ), request_id=request.state.request_id, timestamp=datetime.now(UTC),
    )


@router.get("/{audit_id}", operation_id="getAuditLog", response_model=AuditLogResponse)
async def get_audit_log(
    audit_id: UUID,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("audit:read"))],
    repository: Annotated[AuditLogRepository, Depends(get_repository)],
) -> AuditLogResponse:
    log = await repository.get_by_id(audit_id)
    if log is None:
        raise AuditLogNotFound()
    return SuccessResponse(
        data=audit_log_data(log), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
