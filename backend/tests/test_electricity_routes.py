from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.campus_service.electricity import ElectricityBalance
from app.modules.campus_service.electricity_http import ElectricityMutationResult
from app.modules.campus_service.electricity_routes import get_electricity_http_service
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
ROOM_ID = UUID("21000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _user(*, permissions: tuple[str, ...]) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=USER_ID,
        username="student01",
        display_name="张同学",
        email=None,
        department=None,
        status="active",
        roles=(AuthenticatedRole(UUID(int=1), "student", "普通学生"),),
        permissions=permissions,
        last_login_at=None,
        created_at=NOW,
        version=1,
    )


def _client(service: MagicMock, user: AuthenticatedUser | None) -> TestClient:
    application = create_app()

    async def service_override():
        return service

    application.dependency_overrides[get_electricity_http_service] = service_override
    if user is not None:
        async def user_override() -> AuthenticatedUser:
            return user

        application.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(application)


def test_balance_route_returns_strict_contract() -> None:
    service = MagicMock()
    service.get_balance = AsyncMock(
        return_value=ElectricityBalance(
            room_id=ROOM_ID,
            room_name="梅园 · 3号楼 · 301",
            balance=Decimal("42.50"),
            currency="CNY",
            source="mock",
            is_simulated=True,
            updated_at=NOW,
        )
    )
    response = _client(service, _user(permissions=("electricity:read_own",))).get(
        f"/api/v1/electricity/accounts/{ROOM_ID}",
        headers={"X-Request-Id": "balance-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "room_id": str(ROOM_ID),
        "room_name": "梅园 · 3号楼 · 301",
        "balance_cny": "42.50",
        "source": "mock",
        "is_simulated": True,
        "as_of": "2026-07-16T00:00:00Z",
    }
    assert response.headers["X-Request-Id"] == "balance-1"


def test_topup_route_returns_original_idempotent_response() -> None:
    body = {
        "code": "OK",
        "message": "success",
        "data": {"request_id": str(UUID(int=2))},
        "request_id": "topup-original",
        "timestamp": NOW.isoformat(),
    }
    service = MagicMock()
    service.create_topup = AsyncMock(
        return_value=ElectricityMutationResult(201, "topup-original", body)
    )
    response = _client(
        service,
        _user(permissions=("electricity:topup_request:create",)),
    ).post(
        "/api/v1/electricity/topup-requests",
        json={"room_id": str(ROOM_ID), "amount_cny": "20.00"},
        headers={"Idempotency-Key": "topup-1"},
    )

    assert response.status_code == 201
    assert response.json() == body
    assert response.headers["X-Request-Id"] == "topup-original"
    assert service.create_topup.await_args.kwargs["amount"] == Decimal("20.00")


def test_electricity_routes_require_auth_permissions_and_valid_input() -> None:
    service = MagicMock()
    service.get_balance = AsyncMock()
    service.create_topup = AsyncMock()
    anonymous = _client(service, None).get(
        f"/api/v1/electricity/accounts/{ROOM_ID}"
    )
    forbidden = _client(service, _user(permissions=())).get(
        f"/api/v1/electricity/accounts/{ROOM_ID}"
    )
    client = _client(
        service,
        _user(permissions=("electricity:topup_request:create",)),
    )
    missing_key = client.post(
        "/api/v1/electricity/topup-requests",
        json={"room_id": str(ROOM_ID), "amount_cny": "20.00"},
    )
    invalid_amount = client.post(
        "/api/v1/electricity/topup-requests",
        json={"room_id": str(ROOM_ID), "amount_cny": "20.001"},
        headers={"Idempotency-Key": "topup-1"},
    )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    assert missing_key.status_code == 422
    assert invalid_amount.status_code == 422
    service.get_balance.assert_not_awaited()
    service.create_topup.assert_not_awaited()


def test_electricity_operation_ids_are_registered_once() -> None:
    operation_ids = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    assert operation_ids.count("getElectricityBalance") == 1
    assert operation_ids.count("createElectricityTopupRequest") == 1
