import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.models import Permission, Role
from app.modules.platform.rbac_admin import (
    DuplicateRole,
    PermissionNotFound,
    RoleAdminService,
)
from app.modules.platform.rbac_routes import get_role_admin_service
from app.modules.platform.repositories import RbacWriteRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


def _permission() -> Permission:
    return Permission(
        id=uuid4(), code="user:read", name="用户读取", module="platform",
        description=None, created_at=NOW,
    )


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=("role:write",), last_login_at=None,
        created_at=NOW, version=1,
    )


def _service(*, existing=None, permissions=None, flush_error=None):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    session.execute = AsyncMock(
        side_effect=[_Scalar(existing), _Scalar(permissions or [])]
    )
    session.flush = AsyncMock(side_effect=flush_error)
    repository = RbacWriteRepository(session)
    audit = MagicMock()
    service = RoleAdminService(
        session=session, repository=repository, audit_service=audit, now=lambda: NOW
    )
    return service, session, audit


def test_create_role_binds_permissions_and_audits() -> None:
    permission = _permission()
    service, session, audit = _service(permissions=[permission])

    result = asyncio.run(
        service.create_role(
            actor=_actor(), code="helpdesk", name="服务角色",
            description="服务人员", permission_ids=[permission.id],
            request_id="role-create-request",
        )
    )

    assert result.role.code == "helpdesk"
    assert result.permissions == (permission,)
    assert result.user_count == 0
    assert result.role.is_system is False
    session.flush.assert_awaited_once()
    audit.record_success.assert_called_once()
    assert audit.record_success.call_args.kwargs["action"] == "role.create"
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_create_role_rejects_duplicate_missing_permission_and_race() -> None:
    existing = Role(id=uuid4(), code="helpdesk", name="服务角色")
    service, _, _ = _service(existing=existing)
    with pytest.raises(DuplicateRole):
        asyncio.run(
            service.create_role(
                actor=_actor(), code="helpdesk", name="服务角色",
                description=None, permission_ids=[], request_id="request-id-123",
            )
        )

    service, session, _ = _service(permissions=[])
    with pytest.raises(PermissionNotFound):
        asyncio.run(
            service.create_role(
                actor=_actor(), code="helpdesk", name="服务角色",
                description=None, permission_ids=[uuid4()], request_id="request-id-123",
            )
        )
    session.flush.assert_not_awaited()

    permission = _permission()
    service, _, _ = _service(
        permissions=[permission],
        flush_error=IntegrityError("insert", {}, Exception("duplicate")),
    )
    with pytest.raises(DuplicateRole):
        asyncio.run(
            service.create_role(
                actor=_actor(), code="helpdesk", name="服务角色",
                description=None, permission_ids=[permission.id],
                request_id="request-id-123",
            )
        )


def test_create_role_route_returns_201_and_enforces_permission() -> None:
    permission = _permission()
    service = MagicMock(spec=RoleAdminService)
    service.create_role = AsyncMock(
        return_value=__import__("app.modules.platform.repositories", fromlist=["RoleListItem"]).RoleListItem(
            role=Role(
                id=uuid4(), code="helpdesk", name="服务角色", description=None,
                is_system=False, version=1, created_at=NOW, updated_at=NOW,
            ),
            permissions=(permission,), user_count=0,
        )
    )
    app = create_app()
    app.dependency_overrides[get_role_admin_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: _actor()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/roles",
        json={
            "code": "helpdesk", "name": "服务角色",
            "description": None, "permission_ids": [str(permission.id)],
        },
        headers={"X-Request-Id": "role-route-request"},
    )
    assert response.status_code == 201
    assert response.json()["request_id"] == "role-route-request"
    assert response.json()["data"]["code"] == "helpdesk"

    denied_app = create_app()
    denied_app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
        **{**_actor().__dict__, "permissions": ("user:read",)}
    )
    denied = TestClient(denied_app, raise_server_exceptions=False).post(
        "/api/v1/roles",
        json={"code": "helpdesk", "name": "服务角色", "permission_ids": []},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "AUTH_FORBIDDEN"
