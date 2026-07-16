from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.campus_service.work_order_routes import get_work_order_service
from app.modules.campus_service.work_orders import WorkOrderMutationResult
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _user(*, permitted: bool = True) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=USER_ID,
        username="student01",
        display_name="张同学",
        email=None,
        department=None,
        status="active",
        roles=(
            AuthenticatedRole(
                role_id=UUID("90000000-0000-4000-8000-000000000002"),
                code="student",
                name="普通学生",
            ),
        ),
        permissions=("work_order:create",) if permitted else (),
        last_login_at=None,
        created_at=NOW,
        version=1,
    )


def _payload() -> dict[str, object]:
    return {
        "campus_code": "main",
        "dormitory_area": "梅园",
        "building": "3号楼",
        "room": "301",
        "fault_category": "plumbing",
        "description": "洗手池下方持续漏水，需要尽快检修",
        "preferred_start_at": "2026-07-18T09:00:00+08:00",
        "preferred_end_at": "2026-07-18T11:00:00+08:00",
    }


def _client(service: MagicMock, *, user: AuthenticatedUser | None) -> TestClient:
    application = create_app()

    async def service_override():
        return service

    application.dependency_overrides[get_work_order_service] = service_override
    if user is not None:
        async def user_override() -> AuthenticatedUser:
            return user

        application.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(application)


def test_create_work_order_route_returns_original_json_response() -> None:
    body = {
        "code": "OK",
        "message": "success",
        "data": {"id": "70000000-0000-4000-8000-000000000001"},
        "request_id": "work-order-original",
        "timestamp": NOW.isoformat(),
    }
    service = MagicMock()
    service.create = AsyncMock(
        return_value=WorkOrderMutationResult(201, "work-order-original", body)
    )
    response = _client(service, user=_user()).post(
        "/api/v1/work-orders",
        json=_payload(),
        headers={"Idempotency-Key": "create-1", "X-Request-Id": "incoming"},
    )

    assert response.status_code == 201
    assert response.json() == body
    assert response.headers["X-Request-Id"] == "work-order-original"
    call = service.create.await_args.kwargs
    assert call["actor"].user_id == USER_ID
    assert call["command"].campus_code == "main"
    assert call["idempotency_key"] == "create-1"


def test_create_work_order_route_requires_auth_and_permission() -> None:
    service = MagicMock()
    service.create = AsyncMock()
    anonymous = _client(service, user=None).post(
        "/api/v1/work-orders",
        json=_payload(),
        headers={"Idempotency-Key": "create-1"},
    )
    forbidden = _client(service, user=_user(permitted=False)).post(
        "/api/v1/work-orders",
        json=_payload(),
        headers={"Idempotency-Key": "create-1"},
    )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    service.create.assert_not_awaited()


def test_create_work_order_route_validates_contract_and_time_window() -> None:
    service = MagicMock()
    service.create = AsyncMock()
    client = _client(service, user=_user())

    assert client.post("/api/v1/work-orders", json=_payload()).status_code == 422
    invalid = _payload() | {"preferred_end_at": "2026-07-18T08:00:00+08:00"}
    assert client.post(
        "/api/v1/work-orders",
        json=invalid,
        headers={"Idempotency-Key": "create-1"},
    ).status_code == 422
    naive = _payload() | {"preferred_start_at": "2026-07-18T09:00:00"}
    assert client.post(
        "/api/v1/work-orders",
        json=naive,
        headers={"Idempotency-Key": "create-1"},
    ).status_code == 422
    extra = _payload() | {"created_by": str(USER_ID)}
    assert client.post(
        "/api/v1/work-orders",
        json=extra,
        headers={"Idempotency-Key": "create-1"},
    ).status_code == 422
    service.create.assert_not_awaited()


def test_create_work_order_operation_id_is_registered_once() -> None:
    operation_ids = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    assert operation_ids.count("createWorkOrder") == 1
