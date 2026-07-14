from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.audit_routes import get_repository
from app.modules.platform.models import AuditLog
from app.modules.platform.repositories import AuditLogRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _actor(*permissions: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员", email=None,
        department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def _log() -> AuditLog:
    return AuditLog(
        id=uuid4(), actor_user_id=uuid4(), actor_username="admin01",
        action="auth.login", resource_type="user", resource_id="u",
        result="success", request_id="req", before_data={"password": "secret"},
        after_data={"nested": {"token": "secret"}}, created_at=NOW,
    )


def test_audit_routes_return_page_detail_and_redact_snapshots() -> None:
    log = _log()
    repository = MagicMock(spec=AuditLogRepository)
    repository.list_page = AsyncMock(return_value=([log], 1))
    repository.get_by_id = AsyncMock(return_value=log)
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("audit:read")
    client = TestClient(app, raise_server_exceptions=False)

    page = client.get("/api/v1/audit-logs?from=2026-07-13T00:00:00Z", headers={"X-Request-Id": "audit-list"})
    detail = client.get(f"/api/v1/audit-logs/{log.id}")
    assert page.status_code == 200
    assert page.json()["request_id"] == "audit-list"
    assert detail.status_code == 200
    assert detail.json()["data"]["before_data"] == {"password": "***"}
    assert "secret" not in detail.text


def test_audit_routes_enforce_permission_and_not_found() -> None:
    repository = MagicMock(spec=AuditLogRepository)
    repository.get_by_id = AsyncMock(return_value=None)
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("role:read")
    client = TestClient(app, raise_server_exceptions=False)
    forbidden = client.get(f"/api/v1/audit-logs/{uuid4()}")
    assert forbidden.status_code == 403
    app.dependency_overrides[get_authenticated_user] = lambda: _actor("audit:read")
    not_found = client.get(f"/api/v1/audit-logs/{uuid4()}")
    assert not_found.status_code == 404
    assert not_found.json()["code"] == "AUDIT_LOG_NOT_FOUND"
