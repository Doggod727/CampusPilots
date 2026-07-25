from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.campus_service.service_progress import ServiceProgress
from app.modules.campus_service.service_progress_routes import get_service_progress_service
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, 8, 30, tzinfo=UTC)


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=USER_ID,
        username="student01",
        display_name="张同学",
        email=None,
        department=None,
        status="active",
        roles=(AuthenticatedRole(UUID(int=1), "student", "普通学生"),),
        permissions=(),
        last_login_at=None,
        created_at=NOW,
        version=1,
    )


def _client(service: MagicMock, *, authenticated: bool = True) -> TestClient:
    application = create_app()

    async def service_override():
        return service

    application.dependency_overrides[get_service_progress_service] = service_override
    if authenticated:
        async def user_override() -> AuthenticatedUser:
            return _user()

        application.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(application)


def test_progress_route_returns_strict_response_and_request_id() -> None:
    service = MagicMock()
    service.query = AsyncMock(
        return_value=ServiceProgress(
            system_code="student_affairs",
            business_no_masked="******0001",
            status="reviewing",
            status_text="审核中",
            next_action="等待审核结果",
            updated_at=NOW,
            source="mock",
        )
    )
    response = _client(service).post(
        "/api/v1/service-progress/queries",
        json={"system_code": "student_affairs", "business_no": "SA20260001"},
        headers={"X-Request-Id": "progress-route-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "system_code": "student_affairs",
        "business_no_masked": "******0001",
        "status": "reviewing",
        "status_text": "审核中",
        "next_action": "等待审核结果",
        "updated_at": "2026-07-16T08:30:00Z",
        "source": "mock",
    }
    assert response.headers["X-Request-Id"] == "progress-route-1"
    assert service.query.await_args.kwargs["actor_user_id"] == USER_ID


def test_progress_route_requires_auth_and_validates_body() -> None:
    service = MagicMock()
    service.query = AsyncMock()
    anonymous = _client(service, authenticated=False).post(
        "/api/v1/service-progress/queries",
        json={"system_code": "student_affairs", "business_no": "SA20260001"},
    )
    client = _client(service)
    short = client.post(
        "/api/v1/service-progress/queries",
        json={"system_code": "student_affairs", "business_no": "123"},
    )
    extra = client.post(
        "/api/v1/service-progress/queries",
        json={"system_code": "student_affairs", "business_no": "SA20260001", "user_id": str(USER_ID)},
    )

    assert anonymous.status_code == 401
    assert short.status_code == 422
    assert extra.status_code == 422
    service.query.assert_not_awaited()


def test_progress_operation_id_is_registered_once() -> None:
    operation_ids = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    assert operation_ids.count("queryExternalServiceProgress") == 1
