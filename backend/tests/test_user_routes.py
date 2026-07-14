from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.idempotency import IdempotencyConflict
from app.modules.platform.models import Role, User
from app.modules.platform.repositories import UserListItem, UserListPage
from app.modules.platform.user_admin import CreateUserResult
from app.modules.platform.user_roles import UserRoleService
from app.modules.platform.user_routes import (
    get_user_admin_service,
    get_user_repository,
    get_user_role_service,
)


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
    service: MagicMock | None = None,
    role_service: MagicMock | None = None,
) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_user_repository] = lambda: repository
    if service is not None:
        application.dependency_overrides[get_user_admin_service] = lambda: service
    if role_service is not None:
        application.dependency_overrides[get_user_role_service] = lambda: role_service
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


def test_get_user_returns_openapi_summary_and_no_sensitive_fields() -> None:
    user, role = _listed_user()
    repository = MagicMock()
    repository.get_summary_by_id = AsyncMock(
        return_value=UserListItem(user=user, roles=(role,))
    )
    client = _client(repository, _authenticated_user("user:read"))

    response = client.get(
        f"/api/v1/users/{user.id}",
        headers={"X-Request-Id": "get-user-request-123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "OK"
    assert payload["request_id"] == "get-user-request-123"
    assert payload["data"]["id"] == str(user.id)
    assert payload["data"]["roles"] == [
        {"id": str(role.id), "code": "student", "name": "普通学生"}
    ]
    assert "password_hash" not in payload["data"]
    assert "failed_login_count" not in payload["data"]
    repository.get_summary_by_id.assert_awaited_once_with(user.id)


def test_get_user_rejects_missing_permission() -> None:
    repository = MagicMock()
    repository.get_summary_by_id = AsyncMock()
    client = _client(repository, _authenticated_user("role:read"))

    response = client.get(f"/api/v1/users/{uuid4()}")

    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_FORBIDDEN"
    repository.get_summary_by_id.assert_not_called()


def test_get_user_rejects_missing_bearer_before_repository() -> None:
    repository = MagicMock()
    repository.get_summary_by_id = AsyncMock()
    client = _client(repository)

    response = client.get(f"/api/v1/users/{uuid4()}")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_UNAUTHORIZED"
    repository.get_summary_by_id.assert_not_called()


def test_get_user_returns_not_found_for_missing_or_soft_deleted_user() -> None:
    repository = MagicMock()
    repository.get_summary_by_id = AsyncMock(return_value=None)
    client = _client(repository, _authenticated_user("user:read"))

    response = client.get(
        f"/api/v1/users/{uuid4()}",
        headers={"X-Request-Id": "missing-user-request-123"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "USER_NOT_FOUND"
    assert response.headers["X-Request-Id"] == "missing-user-request-123"
    repository.get_summary_by_id.assert_awaited_once()


def test_get_user_validates_uuid_before_repository() -> None:
    repository = MagicMock()
    repository.get_summary_by_id = AsyncMock()
    client = _client(repository, _authenticated_user("user:read"))

    response = client.get("/api/v1/users/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    repository.get_summary_by_id.assert_not_called()


def _create_payload(role_id: object | None = None) -> dict[str, object]:
    return {
        "username": "student02",
        "password": "DemoPass123!",
        "display_name": "李同学",
        "email": "student02@example.edu",
        "department": "计算机学院",
        "role_ids": [str(role_id or uuid4())],
    }


def test_create_user_returns_201_and_forwards_actor_and_idempotency_key() -> None:
    repository = MagicMock()
    service = MagicMock()
    service.create_user = AsyncMock(
        return_value=CreateUserResult(
            status_code=201,
            request_id="create-user-request-123",
            body={
                "code": "OK",
                "message": "success",
                "data": {"id": str(uuid4())},
                "request_id": "create-user-request-123",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    )
    current_user = _authenticated_user("user:write")
    client = _client(repository, current_user, service)
    payload = _create_payload()

    response = client.post(
        "/api/v1/users",
        json=payload,
        headers={
            "Idempotency-Key": "create-user-key",
            "X-Request-Id": "create-user-request-123",
        },
    )

    assert response.status_code == 201
    assert response.headers["X-Request-Id"] == "create-user-request-123"
    assert response.json()["data"]["id"]
    kwargs = service.create_user.await_args.kwargs
    assert kwargs["actor"] is current_user
    assert kwargs["idempotency_key"] == "create-user-key"
    assert kwargs["request_body"]["username"] == "student02"


def test_create_user_replays_service_response_and_preserves_request_id() -> None:
    repository = MagicMock()
    service = MagicMock()
    body = {
        "code": "OK",
        "message": "success",
        "data": {"id": str(uuid4())},
        "request_id": "first-request-123",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    service.create_user = AsyncMock(
        return_value=CreateUserResult(
            status_code=201,
            request_id="first-request-123",
            body=body,
        )
    )
    client = _client(repository, _authenticated_user("user:write"), service)

    response = client.post(
        "/api/v1/users",
        json=_create_payload(),
        headers={"Idempotency-Key": "create-user-key"},
    )

    assert response.status_code == 201
    assert response.json() == body
    assert response.headers["X-Request-Id"] == "first-request-123"


def test_create_user_maps_idempotency_conflict_to_409() -> None:
    repository = MagicMock()
    service = MagicMock()
    service.create_user = AsyncMock(side_effect=IdempotencyConflict())
    client = _client(repository, _authenticated_user("user:write"), service)

    response = client.post(
        "/api/v1/users",
        json=_create_payload(),
        headers={"Idempotency-Key": "create-user-key"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_create_user_requires_write_permission_and_key_validation() -> None:
    repository = MagicMock()
    service = MagicMock()
    service.create_user = AsyncMock()
    client = _client(repository, _authenticated_user("user:read"), service)

    forbidden = client.post("/api/v1/users", json=_create_payload())
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "AUTH_FORBIDDEN"
    service.create_user.assert_not_called()

    validation_client = _client(repository, _authenticated_user("user:write"), service)
    invalid = validation_client.post(
        "/api/v1/users",
        json={**_create_payload(), "password": "short"},
        headers={"Idempotency-Key": "short"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"


def test_replace_user_roles_returns_updated_summary_and_forwards_actor() -> None:
    repository = MagicMock()
    role_service = MagicMock(spec=UserRoleService)
    user, role = _listed_user()
    role_service.replace_user_roles = AsyncMock(
        return_value=UserListItem(user=user, roles=(role,))
    )
    actor = _authenticated_user("user:role:assign")
    client = _client(repository, actor, role_service=role_service)

    response = client.put(
        f"/api/v1/users/{user.id}/roles",
        json={"role_ids": [str(role.id)], "version": user.version},
        headers={"X-Request-Id": "replace-role-request-123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"] == "replace-role-request-123"
    assert payload["data"]["roles"][0]["code"] == "student"
    assert "password_hash" not in response.text
    kwargs = role_service.replace_user_roles.await_args.kwargs
    assert kwargs["actor"] is actor
    assert kwargs["user_id"] == user.id
    assert kwargs["expected_version"] == user.version


def test_replace_user_roles_requires_permission_and_validates_payload() -> None:
    repository = MagicMock()
    role_service = MagicMock(spec=UserRoleService)
    client = _client(repository, _authenticated_user("user:read"), role_service)

    forbidden = client.put(
        f"/api/v1/users/{uuid4()}/roles",
        json={"role_ids": [str(uuid4())], "version": 1},
    )
    assert forbidden.status_code == 403
    role_service.replace_user_roles.assert_not_called()

    validation_client = _client(
        repository,
        _authenticated_user("user:role:assign"),
        role_service=role_service,
    )
    invalid = validation_client.put(
        f"/api/v1/users/{uuid4()}/roles",
        json={"role_ids": [], "version": 0, "unexpected": True},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"
    role_service.replace_user_roles.assert_not_called()
