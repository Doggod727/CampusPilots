from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.platform.audit import redact
from app.modules.platform.models import AuditLog
from app.shared.responses import SuccessResponse


class AuditLogData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    actor_user_id: UUID | None
    actor_username: str | None
    action: str
    resource_type: str
    resource_id: str | None
    result: str
    request_id: str
    ip_address: str | None
    user_agent: str | None
    before_data: dict[str, object] | None
    after_data: dict[str, object] | None
    error_code: str | None
    created_at: datetime


class AuditLogPageData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[AuditLogData]
    pagination: dict[str, int]


AuditLogResponse = SuccessResponse[AuditLogData]
AuditLogPageResponse = SuccessResponse[AuditLogPageData]


def audit_log_data(log: AuditLog) -> AuditLogData:
    return AuditLogData(
        id=log.id, actor_user_id=log.actor_user_id, actor_username=log.actor_username,
        action=log.action, resource_type=log.resource_type, resource_id=log.resource_id,
        result=log.result, request_id=log.request_id, ip_address=log.ip_address,
        user_agent=log.user_agent, before_data=redact(log.before_data),
        after_data=redact(log.after_data), error_code=log.error_code,
        created_at=log.created_at,
    )
