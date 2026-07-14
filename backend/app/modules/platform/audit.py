from collections.abc import Mapping
from uuid import UUID

from app.modules.platform.models import AuditLog
from app.modules.platform.repositories import AuditLogRepository

REDACTED_VALUE = "***"
SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "authorization",
    "cookie",
    "apikey",
    "secret",
)


def redact(payload: Mapping[str, object] | None) -> dict[str, object] | None:
    """Return a recursively redacted copy suitable for JSON audit snapshots."""

    if payload is None:
        return None
    return {key: _redact_value(key, value) for key, value in payload.items()}


def _redact_value(key: str, value: object) -> object:
    if _is_sensitive_key(key):
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        return {
            nested_key: _redact_value(nested_key, nested_value)
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            {
                nested_key: _redact_value(nested_key, nested_value)
                for nested_key, nested_value in item.items()
            }
            if isinstance(item, Mapping)
            else _redact_list_item(item)
            for item in value
        ]
    return value


def _redact_list_item(value: object) -> object:
    if isinstance(value, list):
        return [_redact_list_item(item) for item in value]
    if isinstance(value, Mapping):
        return {
            nested_key: _redact_value(nested_key, nested_value)
            for nested_key, nested_value in value.items()
        }
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized_key = key.lower().replace("_", "").replace("-", "")
    return any(part in normalized_key for part in SENSITIVE_KEY_PARTS)


class AuditService:
    """Create sanitized audit events in the caller-owned transaction."""

    def __init__(self, repository: AuditLogRepository) -> None:
        self._repository = repository

    def record_success(
        self,
        *,
        action: str,
        resource_type: str,
        request_id: str,
        actor_user_id: UUID | None = None,
        actor_username: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        before_data: Mapping[str, object] | None = None,
        after_data: Mapping[str, object] | None = None,
    ) -> AuditLog:
        return self._record(
            action=action,
            resource_type=resource_type,
            request_id=request_id,
            result="success",
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            before_data=before_data,
            after_data=after_data,
            error_code=None,
        )

    def record_failure(
        self,
        *,
        action: str,
        resource_type: str,
        request_id: str,
        error_code: str,
        actor_user_id: UUID | None = None,
        actor_username: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        before_data: Mapping[str, object] | None = None,
        after_data: Mapping[str, object] | None = None,
    ) -> AuditLog:
        return self._record(
            action=action,
            resource_type=resource_type,
            request_id=request_id,
            result="failure",
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            before_data=before_data,
            after_data=after_data,
            error_code=error_code,
        )

    def _record(
        self,
        *,
        action: str,
        resource_type: str,
        request_id: str,
        result: str,
        actor_user_id: UUID | None,
        actor_username: str | None,
        resource_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
        before_data: Mapping[str, object] | None,
        after_data: Mapping[str, object] | None,
        error_code: str | None,
    ) -> AuditLog:
        audit_log = AuditLog(
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            before_data=redact(before_data),
            after_data=redact(after_data),
            error_code=error_code,
        )
        self._repository.add(audit_log)
        return audit_log
