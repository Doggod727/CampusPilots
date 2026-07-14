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
from app.modules.platform.models import Permission, Role
from app.modules.platform.rbac_permissions import (
    PermissionNotFound,
    ResourceVersionConflict,
    RoleNotFound,
    RolePermissionService,
)
from app.modules.platform.rbac_routes import get_role_permission_service
from app.modules.platform.repositories import RbacWriteRepository, RoleListItem

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _permission(code: str) -> Permission:
    return Permission(
        id=uuid4(), code=code, name=code, module="platform",
        description=None, created_at=NOW,
    )


def _role() -> Role:
    return Role(
        id=uuid4(), code="helpdesk", name="服务角色", description=None,
        is_system=False, version=2, created_at=NOW, updated_at=NOW,
    )


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=("role:permission:assign",), last_login_at=None,
        created_at=NOW, version=1,
    )


def _service(item: RoleListItem | None, permissions: list[Permission]):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    repository = MagicMock(spec=RbacWriteRepository)
    repository.get_role = AsyncMock(return_value=item)
    repository.get_permissions_by_ids = AsyncMock(return_value=permissions)
    repository.update_role_if_version = AsyncMock(return_value=True)
    repository.clear_role_permissions = AsyncMock(return_value=1)
    repository.add_role_permissions = MagicMock()
    audit = MagicMock()
    service = RolePermissionService(
        session=session, repository=repository, audit_service=audit, now=lambda: NOW
    )
    return service, repository, audit


def test_replace_permissions_rebuilds_set_and_audits() -> None:
    role = _role()
    old = _permission("user:read")
    new = [_permission("role:read"), _permission("user:write")]
    service, repository, audit = _service(
        RoleListItem(role=role, permissions=(old,), user_count=2), new
    )
    result = asyncio.run(
        service.replace_permissions(
            actor=_actor(), role_id=role.id, expected_version=2,
            permission_ids=[permission.id for permission in new],
            request_id="permission-replace-request",
        )
    )
    assert result.permissions == tuple(new)
    assert result.role.version == 3
    repository.clear_role_permissions.assert_awaited_once_with(role.id)
    repository.add_role_permissions.assert_called_once_with(
        role.id, [permission.id for permission in new]
    )
    assert audit.record_success.call_args.kwargs["action"] == "role.permissions.replace"


def test_replace_permissions_rejects_role_permission_and_version_errors() -> None:
    service, _, _ = _service(None, [])
    with pytest.raises(RoleNotFound):
        asyncio.run(
            service.replace_permissions(
                actor=_actor(), role_id=uuid4(), expected_version=1,
                permission_ids=[], request_id="request-id-123",
            )
        )
    role = _role()
    old = _permission("user:read")
    service, repository, _ = _service(
        RoleListItem(role=role, permissions=(old,), user_count=1), []
    )
    with pytest.raises(ResourceVersionConflict):
        asyncio.run(
            service.replace_permissions(
                actor=_actor(), role_id=role.id, expected_version=1,
                permission_ids=[], request_id="request-id-123",
            )
        )
    role.version = 2
    service, repository, _ = _service(
        RoleListItem(role=role, permissions=(old,), user_count=1), []
    )
    with pytest.raises(PermissionNotFound):
        asyncio.run(
            service.replace_permissions(
                actor=_actor(), role_id=role.id, expected_version=2,
                permission_ids=[uuid4()], request_id="request-id-123",
            )
        )
    repository.clear_role_permissions.assert_not_awaited()


def test_replace_permissions_route_enforces_permission_and_returns_summary() -> None:
    role = _role()
    permission = _permission("user:read")
    service = MagicMock(spec=RolePermissionService)
    service.replace_permissions = AsyncMock(
        return_value=RoleListItem(role=role, permissions=(permission,), user_count=2)
    )
    app = create_app()
    app.dependency_overrides[get_role_permission_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: _actor()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.put(
        f"/api/v1/roles/{role.id}/permissions",
        json={"permission_ids": [str(permission.id)], "version": 2},
        headers={"X-Request-Id": "permission-route-request"},
    )
    assert response.status_code == 200
    assert response.json()["request_id"] == "permission-route-request"
    assert response.json()["data"]["permissions"][0]["code"] == "user:read"

    invalid = client.put(
        f"/api/v1/roles/{role.id}/permissions",
        json={"permission_ids": [str(permission.id), str(permission.id)], "version": 2},
    )
    assert invalid.status_code == 422
    service.replace_permissions.assert_awaited_once()
