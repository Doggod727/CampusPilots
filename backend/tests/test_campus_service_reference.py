import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.campus_service.models import (
    Campus,
    Department,
    DepartmentContact,
    GuideCategory,
)
from app.modules.campus_service.reference import (
    CampusReferenceService,
    DepartmentNotFound,
    DepartmentService,
)
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    ContactListQuery,
    DepartmentListQuery,
    DepartmentRepository,
)

AS_OF = date(2026, 7, 16)


class _ScalarResult:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[object]:
        return self._items

    def scalar_one_or_none(self) -> object | None:
        return self._items[0] if self._items else None


def _session(*results: _ScalarResult) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    return session


def _sql(session: MagicMock, index: int = 0) -> str:
    statement = session.execute.call_args_list[index].args[0]
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _department(**overrides: object) -> Department:
    values: dict[str, object] = {
        "id": uuid4(),
        "code": "student_affairs",
        "name": "学生事务中心",
        "description": "学生综合事务",
        "enabled": True,
    }
    values.update(overrides)
    return Department(**values)


def _contact(department_id: UUID, **overrides: object) -> DepartmentContact:
    values: dict[str, object] = {
        "id": uuid4(),
        "department_id": department_id,
        "campus_code": "main",
        "contact_name": "王老师",
        "office_name": "综合窗口",
        "phone": "010-55550001",
        "email": "student@example.edu.cn",
        "location": "行政楼 101",
        "office_hours": "工作日",
        "valid_from": date(2026, 1, 1),
        "valid_until": None,
        "enabled": True,
    }
    values.update(overrides)
    return DepartmentContact(**values)


def test_reference_repository_filters_enabled_and_uses_stable_ordering() -> None:
    campus = Campus(code="main", name="主校区", enabled=True, sort_order=10)
    category = GuideCategory(id=uuid4(), code="campus_life", name="校园生活", sort_order=30)
    session = _session(_ScalarResult([campus]), _ScalarResult([category]))
    repository = CampusReferenceRepository(session)

    assert asyncio.run(repository.list_enabled_campuses()) == (campus,)
    assert asyncio.run(repository.list_enabled_categories()) == (category,)

    campus_sql = _sql(session, 0)
    category_sql = _sql(session, 1)
    assert "campuses.enabled IS true" in campus_sql
    assert "ORDER BY campus_service.campuses.sort_order, campus_service.campuses.code" in campus_sql
    assert "guide_categories.enabled IS true" in category_sql
    assert "ORDER BY campus_service.guide_categories.sort_order, campus_service.guide_categories.code" in category_sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_reference_repository_getters_only_return_enabled_records() -> None:
    campus = Campus(code="main", name="主校区")
    category = GuideCategory(id=uuid4(), code="campus_life", name="校园生活")
    session = _session(_ScalarResult([campus]), _ScalarResult([category]))
    repository = CampusReferenceRepository(session)

    assert asyncio.run(repository.get_enabled_campus("main")) is campus
    assert asyncio.run(repository.get_enabled_category("campus_life")) is category
    assert "campuses.enabled IS true" in _sql(session, 0)
    assert "guide_categories.enabled IS true" in _sql(session, 1)


def test_department_search_uses_literal_query_and_active_campus_exists() -> None:
    department = _department()
    session = _session(_ScalarResult([department]))
    repository = DepartmentRepository(session)

    result = asyncio.run(
        repository.list_departments(
            DepartmentListQuery(q="  事务%_  ", campus_code="main", as_of=AS_OF)
        )
    )

    assert result == (department,)
    sql = _sql(session)
    assert "departments.enabled IS true" in sql
    assert "事务" in sql and "\\\\%" in sql and "\\\\_" in sql
    assert sql.count(" ESCAPE ") == 3
    assert "EXISTS (SELECT 1" in sql
    assert "department_contacts.campus_code = 'main'" in sql
    assert "department_contacts.enabled IS true" in sql
    assert "department_contacts.valid_from <= '2026-07-16'" in sql
    assert "department_contacts.valid_until IS NULL" in sql
    assert "department_contacts.valid_until >= '2026-07-16'" in sql
    assert "ORDER BY campus_service.departments.name, campus_service.departments.code" in sql


def test_blank_department_search_does_not_add_ilike() -> None:
    session = _session(_ScalarResult([]))
    repository = DepartmentRepository(session)

    assert asyncio.run(
        repository.list_departments(
            DepartmentListQuery(q="   ", campus_code=None, as_of=AS_OF)
        )
    ) == ()
    assert "ILIKE" not in _sql(session)
    assert "EXISTS" not in _sql(session)


def test_contact_query_applies_inclusive_validity_and_filters() -> None:
    department_id = uuid4()
    contact = _contact(department_id)
    session = _session(_ScalarResult([contact]))
    repository = DepartmentRepository(session)

    result = asyncio.run(
        repository.list_active_contacts(
            ContactListQuery(
                department_id=department_id,
                campus_code="main",
                as_of=AS_OF,
            )
        )
    )

    assert result == (contact,)
    sql = _sql(session)
    assert f"department_contacts.department_id = '{department_id}'" in sql
    assert "department_contacts.campus_code = 'main'" in sql
    assert "valid_from <= '2026-07-16'" in sql
    assert "valid_until >= '2026-07-16'" in sql
    assert "ORDER BY campus_service.department_contacts.campus_code" in sql
    session.commit.assert_not_called()


def test_reference_and_department_services_map_dtos_and_inject_date() -> None:
    campus = Campus(code="main", name="主校区", address="大学路", sort_order=10)
    category = GuideCategory(id=uuid4(), code="campus_life", name="校园生活", sort_order=30)
    reference_repository = MagicMock()
    reference_repository.list_enabled_campuses = AsyncMock(return_value=(campus,))
    reference_repository.list_enabled_categories = AsyncMock(return_value=(category,))
    reference_service = CampusReferenceService(reference_repository)

    assert asyncio.run(reference_service.list_campuses())[0].code == "main"
    assert asyncio.run(reference_service.list_categories())[0].code == "campus_life"

    department = _department()
    contact = _contact(department.id)
    repository = MagicMock()
    repository.list_departments = AsyncMock(return_value=(department,))
    repository.get_department = AsyncMock(return_value=department)
    repository.list_active_contacts = AsyncMock(return_value=(contact,))
    service = DepartmentService(repository, today_provider=lambda: AS_OF)

    items = asyncio.run(service.list_departments(q="事务", campus_code="main"))
    detail = asyncio.run(service.get_department(department.id, campus_code="main"))
    contacts = asyncio.run(service.list_contacts(campus_code="main"))

    assert items[0].id == department.id
    assert detail.department.code == "student_affairs"
    assert detail.contacts[0].phone == "010-55550001"
    assert contacts[0].department_id == department.id
    query = repository.list_departments.await_args.args[0]
    assert query.as_of == AS_OF and query.q == "事务" and query.campus_code == "main"
    detail_query = repository.list_active_contacts.await_args_list[0].args[0]
    assert detail_query.department_id == department.id and detail_query.as_of == AS_OF


def test_department_service_hides_disabled_or_missing_department() -> None:
    repository = MagicMock()
    repository.get_department = AsyncMock(return_value=None)
    repository.list_active_contacts = AsyncMock()
    service = DepartmentService(repository, today_provider=lambda: AS_OF)

    with pytest.raises(DepartmentNotFound) as exc_info:
        asyncio.run(service.get_department(uuid4()))

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "DEPARTMENT_NOT_FOUND"
    repository.list_active_contacts.assert_not_awaited()
