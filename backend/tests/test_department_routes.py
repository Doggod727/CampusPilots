from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.campus_service.department_routes import get_department_service
from app.modules.campus_service.reference import (
    DepartmentContactDTO,
    DepartmentDTO,
    DepartmentDetailDTO,
    DepartmentNotFound,
)
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

DEPARTMENT_ID = UUID("10000000-0000-4000-8000-000000000001")
CONTACT_ID = UUID("20000000-0000-4000-8000-000000000001")


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


def _department() -> DepartmentDTO:
    return DepartmentDTO(
        id=DEPARTMENT_ID,
        code="student_affairs",
        name="学生事务中心",
        description="学生综合事务",
    )


def _contact() -> DepartmentContactDTO:
    return DepartmentContactDTO(
        id=CONTACT_ID,
        department_id=DEPARTMENT_ID,
        campus_code="main",
        contact_name="王老师",
        office_name="综合窗口",
        phone="010-55550001",
        email="student@example.edu.cn",
        location="行政楼 101",
        office_hours="工作日",
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )


def _client(service: MagicMock, *, authenticated: bool = True) -> TestClient:
    application = create_app()

    async def service_override():
        return service

    application.dependency_overrides[get_department_service] = service_override
    if authenticated:
        async def user_override() -> AuthenticatedUser:
            return _user()

        application.dependency_overrides[get_authenticated_user] = user_override
    return TestClient(application)


def test_list_departments_returns_contract_data_and_forwards_filters() -> None:
    service = MagicMock()
    service.list_departments = AsyncMock(return_value=(_department(),))
    client = _client(service)

    response = client.get(
        "/api/v1/departments",
        params={"q": "事务", "campus_code": "main"},
        headers={"X-Request-Id": "department-list-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "department-list-1"
    assert response.json()["data"] == {
        "items": [
            {
                "id": str(DEPARTMENT_ID),
                "code": "student_affairs",
                "name": "学生事务中心",
                "description": "学生综合事务",
            }
        ]
    }
    service.list_departments.assert_awaited_once_with(q="事务", campus_code="main")


def test_department_detail_and_contacts_return_only_service_results() -> None:
    contact = _contact()
    service = MagicMock()
    service.get_department = AsyncMock(
        return_value=DepartmentDetailDTO(
            department=_department(),
            contacts=(contact,),
        )
    )
    service.list_contacts = AsyncMock(return_value=(contact,))
    client = _client(service)

    detail_response = client.get(
        f"/api/v1/departments/{DEPARTMENT_ID}",
        params={"campus_code": "main"},
    )
    contacts_response = client.get(
        "/api/v1/department-contacts",
        params={"department_id": str(DEPARTMENT_ID), "campus_code": "main"},
    )

    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["contacts"][0]["id"] == str(CONTACT_ID)
    assert contacts_response.status_code == 200
    assert contacts_response.json()["data"]["items"][0]["valid_until"] is None
    service.get_department.assert_awaited_once_with(DEPARTMENT_ID, campus_code="main")
    service.list_contacts.assert_awaited_once_with(
        department_id=DEPARTMENT_ID,
        campus_code="main",
    )


def test_department_lists_can_be_empty() -> None:
    service = MagicMock()
    service.list_departments = AsyncMock(return_value=())
    service.list_contacts = AsyncMock(return_value=())
    client = _client(service)

    assert client.get("/api/v1/departments").json()["data"] == {"items": []}
    assert client.get("/api/v1/department-contacts").json()["data"] == {"items": []}


def test_department_not_found_uses_safe_error_code() -> None:
    service = MagicMock()
    service.get_department = AsyncMock(side_effect=DepartmentNotFound())
    client = _client(service)

    response = client.get(f"/api/v1/departments/{DEPARTMENT_ID}")

    assert response.status_code == 404
    assert response.json()["code"] == "DEPARTMENT_NOT_FOUND"
    assert str(DEPARTMENT_ID) not in response.text


def test_department_routes_require_authentication_without_service_permission() -> None:
    service = MagicMock()
    service.list_departments = AsyncMock(return_value=())
    client = _client(service, authenticated=False)

    response = client.get("/api/v1/departments")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_UNAUTHORIZED"
    service.list_departments.assert_not_awaited()


def test_department_route_parameter_validation_matches_openapi() -> None:
    service = MagicMock()
    service.list_departments = AsyncMock(return_value=())
    service.list_contacts = AsyncMock(return_value=())
    client = _client(service)

    assert client.get("/api/v1/departments", params={"q": "x" * 101}).status_code == 422
    assert client.get(
        "/api/v1/departments", params={"campus_code": "x" * 31}
    ).status_code == 422
    assert client.get(
        "/api/v1/department-contacts", params={"department_id": "not-a-uuid"}
    ).status_code == 422


def test_department_operation_ids_are_registered_once() -> None:
    operation_ids = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    for operation_id in (
        "listDepartments",
        "getDepartment",
        "listDepartmentContacts",
    ):
        assert operation_ids.count(operation_id) == 1
