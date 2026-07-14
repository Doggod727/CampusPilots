from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.models import Role, User
from app.modules.platform.repositories import UserListItem, UserListPage
from app.modules.platform.user_routes import get_user_repository


def _authenticated_user(*permissions: str) -> AuthenticatedUser:
    now = datetime.now(UTC)
    return AuthenticatedUser(
        user_id=uuid4(),
        username="admin01",
        display_name="管理员",
        email=None,
        department=None,
        status="active",
        roles=(
            AuthenticatedRole(
                role_id=uuid4(),
                code="super_admin",
                name="超级管理员",
            ),
        ),
        permissions=permissions,
        last_login_at=now,
        created_at=now,
        version=1,
    )


def _listed_user() -> tuple[User, Role]:
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        username="student01",
        password_hash="not-returned",
        display_name="张同学",
        email="student01@example.edu",
        department="计算机学院",
        status="active",
        failed_login_count=0,
        last_login_at=None,
        created_at=now,
        version=1,
    )
    role = Role(id=uuid4(), code="student", name="普通学生")
    return user, role


def _client(
    repository: MagicMock,
    current_user: AuthenticatedUser | None = None,
) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_user_repository] = lambda: repository
    if current_user is not None:
        application.dependency_overrides[get_authenticated_user] = lambda: current_user
    return TestClient(application, raise_server_exceptions=False)


def test_list_users_returns_openapi_page_and_no_sensitive_fields() -> None:
    user, role = _listed_user()
    repository = MagicMock()
    repository.list_page = AsyncMock(
        return_value=UserListPage(
            items=(UserListItem(user=user, roles=(role,)),),
            total=21,
        )
    )
    client = _client(repository, _authenticated_user("user:read"))

    response = client.get(
        "/api/v1/users",
        params={"page": 2, "page_size": 10, "q": "student", "sort": "username"},
        headers={"X-Request-Id": "list-users-request-123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "OK"
    assert payload["request_id"] == "list-users-request-123"
    assert payload["data"]["pagination"] == {
        "page": 2,
        "page_size": 10,
        "total": 21,
        "total_pages": 3,
    }
    assert payload["data"]["items"][0]["username"] == "student01"
    assert payload["data"]["items"][0]["roles"][0]["code"] == "student"
    assert "password_hash" not in payload["data"]["items"][0]
    assert "failed_login_count" not in payload["data"]["items"][0]
    query = repository.list_page.call_args.args[0]
    assert (query.page, query.page_size, query.q, query.sort) == (2, 10, "student", "username")


def test_list_users_rejects_missing_permission() -> None:
    repository = MagicMock()
    repository.list_page = AsyncMock()
    client = _client(repository, _authenticated_user("role:read"))

    response = client.get("/api/v1/users")

    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_FORBIDDEN"
    repository.list_page.assert_not_called()


def test_list_users_rejects_missing_bearer_before_repository() -> None:
    repository = MagicMock()
    repository.list_page = AsyncMock()
    client = _client(repository)

    response = client.get("/api/v1/users")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_UNAUTHORIZED"
    repository.list_page.assert_not_called()


def test_list_users_validates_query_before_repository() -> None:
    repository = MagicMock()
    repository.list_page = AsyncMock()
    client = _client(repository, _authenticated_user("user:read"))

    response = client.get("/api/v1/users", params={"page": 0, "sort": "bad"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    repository.list_page.assert_not_called()
