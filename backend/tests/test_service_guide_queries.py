import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.campus_service.guides import GuideNotFound, ServiceGuideService
from app.modules.campus_service.models import (
    Department,
    DepartmentContact,
    GuideApplicability,
    GuideCategory,
    GuideMaterial,
    GuideStep,
    ServiceGuide,
)
from app.modules.campus_service.repositories import (
    GuideDetailRecord,
    GuideRepository,
    GuideSearchPage,
    GuideSearchQuery,
    GuideSearchRow,
)

AS_OF = date(2026, 7, 16)
GUIDE_ID = UUID("40000000-0000-4000-8000-000000000001")
DEPARTMENT_ID = UUID("10000000-0000-4000-8000-000000000001")
CATEGORY_ID = UUID("30000000-0000-4000-8000-000000000001")


class _CountResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _RowsResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _FirstResult:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def first(self) -> tuple[object, ...] | None:
        return self._row


class _ScalarsResult:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def scalars(self) -> "_ScalarsResult":
        return self

    def all(self) -> list[object]:
        return self._items


def _session(*results: object) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    return session


def _sql(session: MagicMock, index: int) -> str:
    statement = session.execute.call_args_list[index].args[0]
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _guide(**overrides: object) -> ServiceGuide:
    values: dict[str, object] = {
        "id": GUIDE_ID,
        "code": "enrollment_certificate",
        "category_id": CATEGORY_ID,
        "department_id": DEPARTMENT_ID,
        "title": "在读证明办理",
        "summary": "办理在读证明",
        "location": "行政楼 101",
        "service_hours": "工作日",
        "source_url": "https://example.edu.cn/guide",
        "status": "published",
        "published_at": datetime(2026, 1, 1, tzinfo=UTC),
        "valid_until": date(2026, 12, 31),
        "version": 1,
        "updated_at": datetime(2026, 7, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return ServiceGuide(**values)


def _category() -> GuideCategory:
    return GuideCategory(
        id=CATEGORY_ID,
        code="student_certificate",
        name="证明办理",
        sort_order=10,
        enabled=True,
    )


def _department() -> Department:
    return Department(
        id=DEPARTMENT_ID,
        code="student_affairs",
        name="学生事务中心",
        description="学生综合事务",
        enabled=True,
    )


def _applicability(student_type: str = "undergraduate") -> GuideApplicability:
    return GuideApplicability(
        guide_id=GUIDE_ID,
        campus_code="main",
        student_type=student_type,
        notes="主校区本科生",
    )


def _search_query(**overrides: object) -> GuideSearchQuery:
    values: dict[str, object] = {
        "page": 2,
        "page_size": 10,
        "q": " 证明% ",
        "category_code": "student_certificate",
        "department_id": DEPARTMENT_ID,
        "campus_code": "main",
        "student_type": "undergraduate",
        "as_of": AS_OF,
    }
    values.update(overrides)
    return GuideSearchQuery(**values)  # type: ignore[arg-type]


def test_search_filters_visibility_audience_and_uses_stable_pagination() -> None:
    guide, category, department = _guide(), _category(), _department()
    session = _session(
        _CountResult(1),
        _RowsResult([(guide, category, department)]),
    )
    repository = GuideRepository(session)

    result = asyncio.run(repository.search_published(_search_query()))

    assert result.total == 1
    assert result.rows == (
        GuideSearchRow(guide=guide, category=category, department=department),
    )
    count_sql = _sql(session, 0)
    page_sql = _sql(session, 1)
    for sql in (count_sql, page_sql):
        assert "service_guides.status = 'published'" in sql
        assert "service_guides.valid_until IS NULL" in sql
        assert "service_guides.valid_until >= '2026-07-16'" in sql
        assert "guide_categories.enabled IS true" in sql
        assert "departments.enabled IS true" in sql
        assert "guide_applicabilities.campus_code = 'main'" in sql
        assert "guide_applicabilities.student_type IN ('undergraduate', 'all')" in sql
        assert "guide_categories.code = 'student_certificate'" in sql
        assert f"service_guides.department_id = '{DEPARTMENT_ID}'" in sql
        assert "service_guides.title ILIKE" in sql
    assert "ORDER BY campus_service.guide_categories.sort_order" in page_sql
    assert "service_guides.updated_at DESC" in page_sql
    assert "LIMIT 10 OFFSET 10" in page_sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


@pytest.mark.parametrize(
    ("campus_code", "student_type", "campus_clause", "student_clause"),
    [
        ("main", "undergraduate", True, True),
        ("main", None, True, False),
        (None, "undergraduate", False, True),
        (None, None, False, False),
    ],
)
def test_search_compiles_all_optional_audience_combinations(
    campus_code: str | None,
    student_type: str | None,
    campus_clause: bool,
    student_clause: bool,
) -> None:
    session = _session(_CountResult(0), _RowsResult([]))
    repository = GuideRepository(session)

    result = asyncio.run(
        repository.search_published(
            _search_query(
                q=None,
                category_code=None,
                department_id=None,
                campus_code=campus_code,
                student_type=student_type,
            )
        )
    )

    assert result == GuideSearchPage(rows=(), total=0)
    sql = _sql(session, 0)
    assert "EXISTS (SELECT 1" in sql
    assert ("guide_applicabilities.campus_code = 'main'" in sql) is campus_clause
    assert ("guide_applicabilities.student_type IN" in sql) is student_clause


def test_detail_uses_four_fixed_queries_and_orders_children() -> None:
    guide, category, department = _guide(), _category(), _department()
    applicability = _applicability()
    material = GuideMaterial(
        id=uuid4(),
        guide_id=GUIDE_ID,
        name="校园卡",
        description=None,
        required=True,
        copies=1,
        condition={},
        sort_order=10,
    )
    step = GuideStep(
        id=uuid4(),
        guide_id=GUIDE_ID,
        step_no=1,
        title="提交申请",
        description="到窗口提交",
        location=None,
        estimated_minutes=10,
    )
    contact = DepartmentContact(
        id=uuid4(),
        department_id=DEPARTMENT_ID,
        campus_code="main",
        office_name="综合窗口",
        location="行政楼 101",
        valid_from=date(2026, 1, 1),
        enabled=True,
    )
    session = _session(
        _FirstResult((guide, category, department, applicability)),
        _ScalarsResult([material]),
        _ScalarsResult([step]),
        _ScalarsResult([contact]),
    )
    repository = GuideRepository(session)

    result = asyncio.run(
        repository.get_published_detail(
            guide_id=GUIDE_ID,
            campus_code="main",
            student_type="undergraduate",
            as_of=AS_OF,
        )
    )

    assert result is not None
    assert result.materials == (material,)
    assert result.steps == (step,)
    assert result.contacts == (contact,)
    assert session.execute.await_count == 4
    detail_sql = _sql(session, 0)
    assert f"service_guides.id = '{GUIDE_ID}'" in detail_sql
    assert "guide_applicabilities.campus_code = 'main'" in detail_sql
    assert "guide_applicabilities.student_type IN ('undergraduate', 'all')" in detail_sql
    assert "CASE WHEN (campus_service.guide_applicabilities.student_type = 'undergraduate')" in detail_sql
    assert "ORDER BY campus_service.guide_materials.sort_order" in _sql(session, 1)
    assert "ORDER BY campus_service.guide_steps.step_no" in _sql(session, 2)
    contact_sql = _sql(session, 3)
    assert "department_contacts.campus_code = 'main'" in contact_sql
    assert "department_contacts.valid_until >= '2026-07-16'" in contact_sql


def test_detail_stops_after_safe_visibility_query_when_missing() -> None:
    session = _session(_FirstResult(None))
    repository = GuideRepository(session)

    result = asyncio.run(
        repository.get_published_detail(
            guide_id=GUIDE_ID,
            campus_code="main",
            student_type="undergraduate",
            as_of=AS_OF,
        )
    )

    assert result is None
    assert session.execute.await_count == 1


def test_service_maps_search_and_detail_dtos_with_injected_date() -> None:
    guide, category, department = _guide(), _category(), _department()
    applicability = _applicability()
    material = GuideMaterial(
        id=uuid4(), guide_id=GUIDE_ID, name="校园卡", required=True,
        copies=1, condition={"campus_codes": ["main"]}, sort_order=10,
    )
    step = GuideStep(
        id=uuid4(), guide_id=GUIDE_ID, step_no=1, title="提交", description="提交申请"
    )
    contact = DepartmentContact(
        id=uuid4(), department_id=DEPARTMENT_ID, campus_code="main",
        office_name="窗口", location="行政楼", valid_from=date(2026, 1, 1),
    )
    row = GuideSearchRow(guide=guide, category=category, department=department)
    repository = MagicMock()
    repository.search_published = AsyncMock(
        return_value=GuideSearchPage(rows=(row,), total=21)
    )
    repository.get_published_detail = AsyncMock(
        return_value=GuideDetailRecord(
            guide=guide,
            category=category,
            department=department,
            applicability=applicability,
            materials=(material,),
            steps=(step,),
            contacts=(contact,),
        )
    )
    service = ServiceGuideService(repository, today_provider=lambda: AS_OF)

    page = asyncio.run(service.search(page=2, page_size=10, campus_code="main"))
    detail = asyncio.run(
        service.get_detail(
            GUIDE_ID,
            campus_code="main",
            student_type="undergraduate",
        )
    )

    assert page.total == 21 and page.total_pages == 3
    assert page.items[0].category.code == "student_certificate"
    assert detail.summary.id == GUIDE_ID
    assert detail.applicability.applicable is True
    assert detail.materials[0].condition == {"campus_codes": ["main"]}
    assert detail.steps[0].step_no == 1
    assert detail.contacts[0].campus_code == "main"
    search_query = repository.search_published.await_args.args[0]
    assert search_query.as_of == AS_OF and search_query.page == 2
    assert repository.get_published_detail.await_args.kwargs["as_of"] == AS_OF


def test_service_returns_same_safe_404_for_every_invisible_detail() -> None:
    repository = MagicMock()
    repository.get_published_detail = AsyncMock(return_value=None)
    service = ServiceGuideService(repository, today_provider=lambda: AS_OF)

    with pytest.raises(GuideNotFound) as exc_info:
        asyncio.run(
            service.get_detail(
                GUIDE_ID,
                campus_code="main",
                student_type="undergraduate",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "GUIDE_NOT_FOUND"
    assert str(GUIDE_ID) not in exc_info.value.message


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page": 0},
        {"page_size": 101},
        {"student_type": "unknown"},
    ],
)
def test_service_rejects_invalid_internal_search_arguments(kwargs: dict[str, object]) -> None:
    repository = MagicMock()
    repository.search_published = AsyncMock()
    service = ServiceGuideService(repository, today_provider=lambda: AS_OF)

    with pytest.raises(ValueError):
        asyncio.run(service.search(**kwargs))

    repository.search_published.assert_not_awaited()
