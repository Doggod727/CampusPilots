from datetime import UTC, datetime
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.campus_service.work_order_routes import get_work_order_service
from app.modules.campus_service.work_order_schemas import (
    PageMetaData,
    WorkOrderData,
    WorkOrderEventListData,
    WorkOrderPageData,
)
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


def _reader() -> AuthenticatedUser:
    return replace(_user(), permissions=("work_order:read",))


def _work_order() -> WorkOrderData:
    return WorkOrderData(
        id=UUID("70000000-0000-4000-8000-000000000001"),
        order_no="WO-20260716-0001",
        created_by=USER_ID,
        campus_code="main",
        dormitory_area="梅园",
        building="3号楼",
        room="301",
        fault_category="plumbing",
        description="洗手池下方持续漏水，需要尽快检修",
        preferred_start_at=NOW,
        preferred_end_at=datetime(2026, 7, 16, 18, tzinfo=UTC),
        status="submitted",
        assigned_to=None,
        assigned_department_id=None,
        rejection_reason=None,
        completion_note=None,
        rating=None,
        version=1,
        submitted_at=NOW,
        accepted_at=None,
        processing_at=None,
        completed_at=None,
        cancelled_at=None,
        rejected_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


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


def test_work_order_read_routes_return_strict_contracts_and_forward_filters() -> None:
    service = MagicMock()
    order = _work_order()
    service.list_visible = AsyncMock(
        return_value=WorkOrderPageData(
            items=[order],
            pagination=PageMetaData(page=1, page_size=10, total=1, total_pages=1),
        )
    )
    service.get_visible = AsyncMock(return_value=order)
    service.list_events = AsyncMock(return_value=WorkOrderEventListData(items=[]))
    client = _client(service, user=_reader())

    listed = client.get(
        "/api/v1/work-orders?page=1&page_size=10&status=submitted&campus_code=main&assigned_to_me=true",
        headers={"X-Request-Id": "orders-list-1"},
    )
    detailed = client.get(f"/api/v1/work-orders/{order.id}")
    timeline = client.get(f"/api/v1/work-orders/{order.id}/events")

    assert listed.status_code == 200
    assert listed.json()["data"]["pagination"] == {
        "page": 1, "page_size": 10, "total": 1, "total_pages": 1
    }
    assert listed.headers["X-Request-Id"] == "orders-list-1"
    assert service.list_visible.await_args.kwargs["assigned_to_me"] is True
    assert detailed.status_code == 200
    assert detailed.json()["data"]["id"] == str(order.id)
    assert timeline.status_code == 200
    assert timeline.json()["data"] == {"items": []}


def test_work_order_read_routes_require_permission_and_validate_queries() -> None:
    service = MagicMock()
    service.list_visible = AsyncMock()
    anonymous = _client(service, user=None).get("/api/v1/work-orders")
    forbidden = _client(service, user=_user(permitted=False)).get("/api/v1/work-orders")
    client = _client(service, user=_reader())
    invalid_page = client.get("/api/v1/work-orders?page=0")
    invalid_status = client.get("/api/v1/work-orders?status=unknown")
    invalid_id = client.get("/api/v1/work-orders/not-a-uuid")

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    assert invalid_page.status_code == 422
    assert invalid_status.status_code == 422
    assert invalid_id.status_code == 422
    service.list_visible.assert_not_awaited()


def test_work_order_read_operation_ids_are_registered_once() -> None:
    operation_ids = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    for operation_id in ("listWorkOrders", "getWorkOrder", "listWorkOrderEvents"):
        assert operation_ids.count(operation_id) == 1


def test_transition_route_accepts_owner_without_staff_permission() -> None:
    body = {
        "code": "OK", "message": "success", "data": {"id": str(_work_order().id)},
        "request_id": "transition-original", "timestamp": NOW.isoformat(),
    }
    service = MagicMock()
    service.transition = AsyncMock(
        return_value=WorkOrderMutationResult(200, "transition-original", body)
    )
    response = _client(service, user=_user(permitted=False)).post(
        f"/api/v1/work-orders/{_work_order().id}/transitions",
        json={"target_status": "cancelled", "reason": "不再需要维修", "version": 1},
        headers={"Idempotency-Key": "transition-1"},
    )
    assert response.status_code == 200
    assert response.json() == body
    assert service.transition.await_args.kwargs["command"].target_status == "cancelled"


def test_transition_route_requires_auth_key_and_strict_payload() -> None:
    service = MagicMock(); service.transition = AsyncMock()
    path = f"/api/v1/work-orders/{_work_order().id}/transitions"
    payload = {"target_status": "accepted", "reason": "接单处理", "version": 1}
    assert _client(service, user=None).post(path, json=payload, headers={"Idempotency-Key": "x"}).status_code == 401
    client = _client(service, user=_user(permitted=False))
    assert client.post(path, json=payload).status_code == 422
    assert client.post(path, json=payload | {"version": 0}, headers={"Idempotency-Key": "x"}).status_code == 422
    assert client.post(path, json=payload | {"extra": True}, headers={"Idempotency-Key": "x"}).status_code == 422
    service.transition.assert_not_awaited()


def test_transition_operation_id_is_registered_once() -> None:
    operation_ids = [route.operation_id for route in create_app().routes if getattr(route, "operation_id", None)]
    assert operation_ids.count("transitionWorkOrder") == 1
