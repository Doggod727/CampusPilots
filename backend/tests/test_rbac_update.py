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
from app.modules.platform.rbac_routes import get_role_update_service
from app.modules.platform.rbac_update import (
    ResourceVersionConflict,
    RoleNotFound,
    RoleUpdateService,
)
from app.modules.platform.repositories import RoleListItem, RbacWriteRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


def _role() -> Role:
    return Role(
        id=uuid4(), code="helpdesk", name="旧名称", description="旧描述",
        is_system=False, version=2, created_at=NOW, updated_at=NOW,
    )


def _item(role: Role) -> RoleListItem:
    permission = Permission(
        id=uuid4(), code="user:read", name="用户读取", module="platform",
        description=None, created_at=NOW,
    )
    return RoleListItem(role=role, permissions=(permission,), user_count=3)


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=("role:write",), last_login_at=None,
        created_at=NOW, version=1,
    )


def _service(item: RoleListItem | None):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    repository = MagicMock(spec=RbacWriteRepository)
    repository.get_role = AsyncMock(return_value=item)
    repository.update_role_if_version = AsyncMock(return_value=True)
    audit = MagicMock()
    service = RoleUpdateService(
        session=session, repository=repository, audit_service=audit, now=lambda: NOW
    )
    return service, session, audit


def test_update_role_changes_safe_fields_and_increments_version() -> None:
    role = _role()
    service, session, audit = _service(_item(role))
    result = asyncio.run(
        service.update_role(
            actor=_actor(), role_id=role.id, expected_version=2,
            changes={"name": "新名称", "description": None},
            request_id="role-update-request",
        )
    )
    assert result.role.name == "新名称"
    assert result.role.description is None
    assert result.role.version == 3
    audit.record_success.assert_called_once()
    assert audit.record_success.call_args.kwargs["action"] == "role.update"
    assert audit.record_success.call_args.kwargs["before_data"]["name"] == "旧名称"
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_update_role_rejects_missing_or_stale_role() -> None:
    service, _, _ = _service(None)
    with pytest.raises(RoleNotFound):
        asyncio.run(
            service.update_role(
                actor=_actor(), role_id=uuid4(), expected_version=1,
                changes={"name": "名称"}, request_id="request-id-123",
            )
        )
    role = _role()
    service, session, _ = _service(_item(role))
    with pytest.raises(ResourceVersionConflict):
        asyncio.run(
            service.update_role(
                actor=_actor(), role_id=role.id, expected_version=1,
                changes={"name": "名称"}, request_id="request-id-123",
            )
        )


def test_update_role_route_returns_200_and_validates_permission() -> None:
    role = _role()
    service = MagicMock(spec=RoleUpdateService)
    service.update_role = AsyncMock(return_value=_item(role))
    app = create_app()
    app.dependency_overrides[get_role_update_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: _actor()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"name": "新名称", "version": 2},
        headers={"X-Request-Id": "role-update-route"},
    )
    assert response.status_code == 200
    assert response.json()["request_id"] == "role-update-route"
    assert response.json()["data"]["user_count"] == 3

    invalid = client.patch(f"/api/v1/roles/{role.id}", json={"version": 2})
    assert invalid.status_code == 422
    service.update_role.assert_awaited_once()
