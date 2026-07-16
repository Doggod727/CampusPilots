from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.campus_service.guide_routes import get_guide_service
from app.modules.campus_service.guides import GuideNotFound
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from tests.test_material_checklist import _detail

GUIDE_ID = UUID("40000000-0000-4000-8000-000000000001")


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=UUID("90000000-0000-4000-8000-000000000001"),
        username="student01",
        display_name="学生一号",
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
        permissions=(),
        last_login_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        version=1,
    )


def _client(service: MagicMock, *, authenticated: bool = True) -> TestClient:
    application = create_app()

    async def service_override():
        return service

    application.dependency_overrides[get_guide_service] = service_override
    if authenticated:
        async def user_override() -> AuthenticatedUser:
            return _user()

        application.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(application, raise_server_exceptions=False)


def test_checklist_route_returns_strict_contract_without_raw_condition() -> None:
    service = MagicMock()
    service.get_detail = AsyncMock(
        return_value=_detail({}, {"campus_codes": ["east"]})
    )
    response = _client(service).get(
        f"/api/v1/service-guides/{GUIDE_ID}/checklist",
        params={"campus_code": "main", "student_type": "undergraduate"},
        headers={"X-Request-Id": "guide-checklist-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "guide-checklist-1"
    assert response.json()["data"]["materials"][0]["inclusion_reason"] == "通用材料"
    assert response.json()["data"]["materials"][1]["included"] is False
    assert "condition" not in response.text
    service.get_detail.assert_awaited_once_with(
        GUIDE_ID,
        campus_code="main",
        student_type="undergraduate",
    )


def test_checklist_route_requires_authentication_and_safe_visibility() -> None:
    service = MagicMock()
    service.get_detail = AsyncMock(side_effect=GuideNotFound())
    response = _client(service).get(
        f"/api/v1/service-guides/{GUIDE_ID}/checklist",
        params={"campus_code": "main", "student_type": "undergraduate"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "GUIDE_NOT_FOUND"
    assert str(GUIDE_ID) not in response.text

    anonymous_service = MagicMock()
    anonymous_service.get_detail = AsyncMock()
    anonymous = _client(anonymous_service, authenticated=False).get(
        f"/api/v1/service-guides/{GUIDE_ID}/checklist",
        params={"campus_code": "main", "student_type": "undergraduate"},
    )
    assert anonymous.status_code == 401
    anonymous_service.get_detail.assert_not_awaited()


def test_checklist_route_validates_all_parameters() -> None:
    service = MagicMock()
    service.get_detail = AsyncMock()
    client = _client(service)
    path = f"/api/v1/service-guides/{GUIDE_ID}/checklist"

    assert client.get(path).status_code == 422
    assert client.get(
        path, params={"campus_code": "x" * 31, "student_type": "undergraduate"}
    ).status_code == 422
    assert client.get(
        path, params={"campus_code": "main", "student_type": "unknown"}
    ).status_code == 422
    assert client.get(
        "/api/v1/service-guides/not-a-uuid/checklist",
        params={"campus_code": "main", "student_type": "undergraduate"},
    ).status_code == 422
    service.get_detail.assert_not_awaited()


def test_checklist_operation_id_is_registered_once() -> None:
    operation_ids = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    assert operation_ids.count("getServiceGuideChecklist") == 1
