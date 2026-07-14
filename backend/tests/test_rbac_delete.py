import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.models import Role
from app.modules.platform.rbac_delete import (
    RoleDeleteService,
    RoleInUse,
    SystemRoleProtected,
)
from app.modules.platform.rbac_routes import get_role_delete_service
from app.modules.platform.rbac_update import RoleNotFound
from app.modules.platform.repositories import RbacWriteRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _role(*, system: bool = False) -> Role:
    return Role(
        id=uuid4(), code="custom_role", name="自定义角色", description=None,
        is_system=system, version=1, created_at=NOW, updated_at=NOW,
    )


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=("role:write",), last_login_at=None,
        created_at=NOW, version=1,
    )


def _service(role: Role | None, assignments: int = 0):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    repository = MagicMock(spec=RbacWriteRepository)
    repository.get_role_for_update = AsyncMock(return_value=role)
    repository.count_role_assignments = AsyncMock(return_value=assignments)
    repository.delete_role = AsyncMock(return_value=role is not None)
    audit = MagicMock()
    service = RoleDeleteService(
        session=session, repository=repository, audit_service=audit, now=lambda: NOW
    )
    return service, session, repository, audit


def test_delete_role_removes_custom_role_and_audits_safe_snapshot() -> None:
    role = _role()
    service, session, repository, audit = _service(role)

    asyncio.run(service.delete_role(actor=_actor(), role_id=role.id, request_id="req"))

    repository.delete_role.assert_awaited_once_with(role.id)
    audit.record_success.assert_called_once()
    assert audit.record_success.call_args.kwargs["action"] == "role.delete"
    assert audit.record_success.call_args.kwargs["after_data"] == {"status": "deleted"}
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("role,assignments,error", [
    (None, 0, RoleNotFound),
    (_role(system=True), 0, SystemRoleProtected),
    (_role(), 1, RoleInUse),
])
def test_delete_role_rejects_unsafe_or_missing_roles(role, assignments, error) -> None:
    service, _, repository, audit = _service(role, assignments)
    role_id = role.id if role is not None else uuid4()

    with pytest.raises(error):
        asyncio.run(service.delete_role(actor=_actor(), role_id=role_id, request_id="req"))

    repository.delete_role.assert_not_called()
    audit.record_success.assert_not_called()


def test_delete_role_route_returns_empty_success_and_enforces_permission() -> None:
    role = _role()
    service = MagicMock(spec=RoleDeleteService)
    service.delete_role = AsyncMock()
    app = create_app()
    app.dependency_overrides[get_role_delete_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: _actor()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.delete(
        f"/api/v1/roles/{role.id}", headers={"X-Request-Id": "delete-route"}
    )

    assert response.status_code == 200
    assert response.json()["data"] == {}
    assert response.json()["request_id"] == "delete-route"
    service.delete_role.assert_awaited_once()

    invalid = client.delete("/api/v1/roles/not-a-uuid")
    assert invalid.status_code == 422
