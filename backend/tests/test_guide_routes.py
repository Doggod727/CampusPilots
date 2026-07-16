from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.campus_service.guide_routes import get_guide_service
from app.modules.campus_service.guides import (
    GuideNotFound,
    GuidePageDTO,
    GuideStepDTO,
)
from app.modules.campus_service.reference import DepartmentContactDTO
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from tests.test_material_checklist import _detail

GUIDE_ID = UUID("40000000-0000-4000-8000-000000000001")
DEPARTMENT_ID = UUID("10000000-0000-4000-8000-000000000001")


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
    return TestClient(application)


def test_guide_list_returns_exact_page_contract_and_forwards_filters() -> None:
    detail = _detail({})
    service = MagicMock()
    service.search = AsyncMock(
        return_value=GuidePageDTO(
            items=(detail.summary,),
            page=2,
            page_size=10,
            total=21,
            total_pages=3,
        )
    )
    response = _client(service).get(
        "/api/v1/service-guides",
        params={
            "page": 2,
            "page_size": 10,
            "q": "证明",
            "category_code": "certificate",
            "department_id": str(DEPARTMENT_ID),
            "campus_code": "main",
            "student_type": "undergraduate",
        },
        headers={"X-Request-Id": "guide-list-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "guide-list-1"
    data = response.json()["data"]
    assert data["pagination"] == {
        "page": 2,
        "page_size": 10,
        "total": 21,
        "total_pages": 3,
    }
    assert data["items"][0] == {
        "id": str(GUIDE_ID),
        "code": "guide",
        "title": "指南",
        "summary": "摘要",
        "category": {"code": "category", "name": "分类"},
        "department": {
            "id": str(DEPARTMENT_ID),
            "code": "department",
            "name": "部门",
            "description": None,
        },
        "location": None,
        "service_hours": None,
        "valid_until": "2027-01-01",
        "updated_at": "2026-07-16T00:00:00Z",
        "version": 1,
    }
    service.search.assert_awaited_once_with(
        page=2,
        page_size=10,
        q="证明",
        category_code="certificate",
        department_id=DEPARTMENT_ID,
        campus_code="main",
        student_type="undergraduate",
    )


def test_guide_list_can_be_empty() -> None:
    service = MagicMock()
    service.search = AsyncMock(
        return_value=GuidePageDTO(
            items=(), page=1, page_size=20, total=0, total_pages=0
        )
    )
    response = _client(service).get("/api/v1/service-guides")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [],
        "pagination": {"page": 1, "page_size": 20, "total": 0, "total_pages": 0},
    }


def test_guide_detail_returns_computed_materials_steps_and_contacts() -> None:
    detail = _detail({"campus_codes": ["main"]})
    detail = replace(
        detail,
        source_url="https://example.edu/guide",
        steps=(
            GuideStepDTO(
                step_no=1,
                title="提交申请",
                description="提交材料",
                location="行政楼",
                estimated_minutes=10,
            ),
        ),
        contacts=(
            DepartmentContactDTO(
                id=UUID("20000000-0000-4000-8000-000000000001"),
                department_id=DEPARTMENT_ID,
                campus_code="main",
                contact_name="王老师",
                office_name="综合窗口",
                phone="010-55550001",
                email="service@example.edu.cn",
                location="行政楼 101",
                office_hours="工作日",
                valid_from=date(2026, 1, 1),
                valid_until=None,
            ),
        ),
    )
    service = MagicMock()
    service.get_detail = AsyncMock(return_value=detail)
    response = _client(service).get(
        f"/api/v1/service-guides/{GUIDE_ID}",
        params={"campus_code": "main", "student_type": "undergraduate"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source_url"] == "https://example.edu/guide"
    assert data["applicability"] == {
        "campus_code": "main",
        "student_type": "undergraduate",
        "applicable": True,
        "notes": "主校区本科生",
    }
    assert data["materials"][0]["included"] is True
    assert data["materials"][0]["inclusion_reason"] == "校区匹配：main"
    assert data["steps"][0]["step_no"] == 1
    assert data["contacts"][0]["office_name"] == "综合窗口"
    assert "condition" not in response.text
    service.get_detail.assert_awaited_once_with(
        GUIDE_ID,
        campus_code="main",
        student_type="undergraduate",
    )


def test_guide_routes_require_auth_and_preserve_safe_404() -> None:
    service = MagicMock()
    service.get_detail = AsyncMock(side_effect=GuideNotFound())
    response = _client(service).get(
        f"/api/v1/service-guides/{GUIDE_ID}",
        params={"campus_code": "main", "student_type": "undergraduate"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "GUIDE_NOT_FOUND"
    assert str(GUIDE_ID) not in response.text

    anonymous_service = MagicMock()
    anonymous_service.search = AsyncMock()
    anonymous = _client(anonymous_service, authenticated=False).get(
        "/api/v1/service-guides"
    )
    assert anonymous.status_code == 401
    anonymous_service.search.assert_not_awaited()


def test_guide_route_parameter_validation_matches_openapi() -> None:
    service = MagicMock()
    service.search = AsyncMock()
    service.get_detail = AsyncMock()
    client = _client(service)

    for params in (
        {"page": 0},
        {"page_size": 101},
        {"q": "x" * 101},
        {"category_code": "x" * 51},
        {"department_id": "not-a-uuid"},
        {"campus_code": "x" * 31},
        {"student_type": "unknown"},
    ):
        assert client.get("/api/v1/service-guides", params=params).status_code == 422

    path = f"/api/v1/service-guides/{GUIDE_ID}"
    assert client.get(path).status_code == 422
    assert client.get(
        path, params={"campus_code": "main", "student_type": "unknown"}
    ).status_code == 422
    service.search.assert_not_awaited()
    service.get_detail.assert_not_awaited()


def test_all_guide_operation_ids_are_registered_once() -> None:
    operation_ids = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    ]
    for operation_id in (
        "listServiceGuides",
        "getServiceGuide",
        "getServiceGuideChecklist",
    ):
        assert operation_ids.count(operation_id) == 1
