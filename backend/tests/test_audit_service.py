from copy import deepcopy
from unittest.mock import MagicMock
from uuid import uuid4

from app.modules.platform.audit import REDACTED_VALUE, AuditService, redact
from app.modules.platform.models import AuditLog
from app.modules.platform.repositories import AuditLogRepository


def test_redact_recursively_copies_payload_and_hides_sensitive_key_variants() -> None:
    payload = {
        "username": "student01",
        "password_hash": "argon2-secret",
        "profile": {
            "accessToken": "access-token-value",
            "api-key": "api-key-value",
        },
        "sessions": [
            {"Cookie": "refresh-cookie", "nested": [{"client_secret": "secret"}]},
            "safe-value",
        ],
    }
    original = deepcopy(payload)

    sanitized = redact(payload)

    assert sanitized == {
        "username": "student01",
        "password_hash": REDACTED_VALUE,
        "profile": {
            "accessToken": REDACTED_VALUE,
            "api-key": REDACTED_VALUE,
        },
        "sessions": [
            {
                "Cookie": REDACTED_VALUE,
                "nested": [{"client_secret": REDACTED_VALUE}],
            },
            "safe-value",
        ],
    }
    assert payload == original
    assert sanitized is not payload
    assert sanitized["profile"] is not payload["profile"]


def test_redact_allows_none() -> None:
    assert redact(None) is None


def test_audit_service_records_sanitized_success_event() -> None:
    repository = MagicMock(spec=AuditLogRepository)
    service = AuditService(repository)
    actor_id = uuid4()
    before_data = {"password": "not-for-storage", "username": "student01"}
    after_data = {"refresh_token": "not-for-storage", "status": "active"}

    audit_log = service.record_success(
        action="auth.login",
        resource_type="user",
        resource_id="student01",
        request_id="request-id-123",
        actor_user_id=actor_id,
        actor_username="student01",
        ip_address="127.0.0.1",
        user_agent="test-agent",
        before_data=before_data,
        after_data=after_data,
    )

    assert isinstance(audit_log, AuditLog)
    assert audit_log.result == "success"
    assert audit_log.error_code is None
    assert audit_log.actor_user_id == actor_id
    assert audit_log.before_data == {
        "password": REDACTED_VALUE,
        "username": "student01",
    }
    assert audit_log.after_data == {
        "refresh_token": REDACTED_VALUE,
        "status": "active",
    }
    assert "not-for-storage" not in repr(audit_log.before_data)
    assert "not-for-storage" not in repr(audit_log.after_data)
    repository.add.assert_called_once_with(audit_log)


def test_audit_service_records_failure_without_exception_text() -> None:
    repository = MagicMock(spec=AuditLogRepository)
    service = AuditService(repository)

    audit_log = service.record_failure(
        action="auth.login",
        resource_type="user",
        resource_id="unknown",
        request_id="request-id-456",
        error_code="INVALID_CREDENTIALS",
        before_data={"authorization": "Bearer sensitive"},
    )

    assert audit_log.result == "failure"
    assert audit_log.error_code == "INVALID_CREDENTIALS"
    assert audit_log.before_data == {"authorization": REDACTED_VALUE}
    repository.add.assert_called_once_with(audit_log)
