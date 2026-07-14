from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.models import Permission, Role
from app.modules.platform.rbac_routes import get_rbac_repository
from app.modules.platform.repositories import RbacReadRepository, RoleListItem

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class _Entities:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


def _role(code: str) -> Role:
    return Role(
        id=uuid4(), code=code, name=code, description=None,
        is_system=code == "student", version=1,
        created_at=NOW, updated_at=NOW,
    )


def _permission(code: str, module: str = "platform") -> Permission:
    return Permission(
        id=uuid4(), code=code, name=code, module=module,
        description=None, created_at=NOW,
    )


def _actor(*permissions: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(), username="admin01", display_name="管理员",
        email=None, department=None, status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=permissions, last_login_at=None, created_at=NOW, version=1,
    )


def test_rbac_repository_lists_roles_with_permissions_and_counts() -> None:
    role_one = _role("admin")
    role_two = _role("student")
    permission = _permission("user:read")
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _Entities([role_one, role_two]),
            _Rows([(role_one.id, permission)]),
            _Rows([(role_one.id, 3)]),
        ]
    )

    result = __import__("asyncio").run(RbacReadRepository(session).list_roles())

    assert [item.role.code for item in result] == ["admin", "student"]
    assert result[0].permissions == (permission,)
    assert result[0].user_count == 3
    assert result[1].user_count == 0
    assert session.execute.await_count == 3
    sql = str(session.execute.call_args_list[1].args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert "JOIN platform.permissions" in sql
    assert "ORDER BY platform.role_permissions.role_id, platform.permissions.code" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_rbac_repository_filters_permissions_by_module() -> None:
    permission = _permission("user:read")
    session = MagicMock()
    session.execute = AsyncMock(return_value=_Entities([permission]))

    import asyncio
    result = asyncio.run(RbacReadRepository(session).list_permissions("platform"))

    assert result == [permission]
    sql = str(session.execute.call_args.args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert "platform.permissions.module = 'platform'" in sql
    assert "ORDER BY platform.permissions.module, platform.permissions.code" in sql


def _client(repository: MagicMock, actor: AuthenticatedUser | None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_rbac_repository] = lambda: repository
    if actor is not None:
        app.dependency_overrides[get_authenticated_user] = lambda: actor
    return TestClient(app, raise_server_exceptions=False)


def test_role_and_permission_routes_return_safe_sorted_envelopes() -> None:
    role = _role("student")
    permission = _permission("user:read")
    repository = MagicMock(spec=RbacReadRepository)
    repository.list_roles = AsyncMock(
        return_value=(RoleListItem(role=role, permissions=(permission,), user_count=2),)
    )
    repository.list_permissions = AsyncMock(return_value=[permission])
    client = _client(repository, _actor("role:read"))

    roles_response = client.get("/api/v1/roles", headers={"X-Request-Id": "roles-request-123"})
    permissions_response = client.get(
        "/api/v1/permissions", params={"module": "platform"},
        headers={"X-Request-Id": "permissions-request-123"},
    )

    assert roles_response.status_code == 200
    assert roles_response.json()["request_id"] == "roles-request-123"
    assert roles_response.json()["data"]["items"][0]["user_count"] == 2
    assert permissions_response.status_code == 200
    assert permissions_response.json()["data"]["items"][0]["code"] == "user:read"
    repository.list_permissions.assert_awaited_once_with("platform")


def test_rbac_routes_require_role_read_permission() -> None:
    repository = MagicMock(spec=RbacReadRepository)
    repository.list_roles = AsyncMock()
    repository.list_permissions = AsyncMock()
    client = _client(repository, _actor("user:read"))

    response = client.get("/api/v1/roles")

    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_FORBIDDEN"
    repository.list_roles.assert_not_called()
