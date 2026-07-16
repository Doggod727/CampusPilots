from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from math import ceil
from uuid import UUID

from app.core.errors import AppError
from app.modules.campus_service.models import DepartmentContact
from app.modules.campus_service.reference import (
    DepartmentContactDTO,
    DepartmentDTO,
    GuideCategoryDTO,
)
from app.modules.campus_service.repositories import (
    GuideDetailRecord,
    GuideRepository,
    GuideSearchQuery,
    GuideSearchRow,
)

STUDENT_TYPES = frozenset(
    {"undergraduate", "postgraduate", "international", "all"}
)


class GuideNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="GUIDE_NOT_FOUND",
            message="办事指南不存在或不适用于当前条件",
        )


@dataclass(frozen=True)
class GuideSummaryDTO:
    id: UUID
    code: str
    title: str
    summary: str
    category: GuideCategoryDTO
    department: DepartmentDTO
    location: str | None
    service_hours: str | None
    valid_until: date | None
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class GuideApplicabilityDTO:
    campus_code: str
    student_type: str
    applicable: bool
    notes: str | None


@dataclass(frozen=True)
class GuideMaterialRawDTO:
    id: UUID
    name: str
    description: str | None
    required: bool
    copies: int
    condition: dict[str, object]
    sort_order: int


@dataclass(frozen=True)
class GuideStepDTO:
    step_no: int
    title: str
    description: str
    location: str | None
    estimated_minutes: int | None


@dataclass(frozen=True)
class GuideDetailDTO:
    summary: GuideSummaryDTO
    source_url: str | None
    applicability: GuideApplicabilityDTO
    materials: tuple[GuideMaterialRawDTO, ...]
    steps: tuple[GuideStepDTO, ...]
    contacts: tuple[DepartmentContactDTO, ...]


@dataclass(frozen=True)
class GuidePageDTO:
    items: tuple[GuideSummaryDTO, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


def _category_dto(row: GuideSearchRow | GuideDetailRecord) -> GuideCategoryDTO:
    category = row.category
    return GuideCategoryDTO(
        id=category.id,
        code=category.code,
        name=category.name,
        sort_order=category.sort_order,
    )


def _department_dto(row: GuideSearchRow | GuideDetailRecord) -> DepartmentDTO:
    department = row.department
    return DepartmentDTO(
        id=department.id,
        code=department.code,
        name=department.name,
        description=department.description,
    )


def _summary_dto(row: GuideSearchRow | GuideDetailRecord) -> GuideSummaryDTO:
    guide = row.guide
    return GuideSummaryDTO(
        id=guide.id,
        code=guide.code,
        title=guide.title,
        summary=guide.summary,
        category=_category_dto(row),
        department=_department_dto(row),
        location=guide.location,
        service_hours=guide.service_hours,
        valid_until=guide.valid_until,
        updated_at=guide.updated_at,
        version=guide.version,
    )


def _contact_dto(item: DepartmentContact) -> DepartmentContactDTO:
    return DepartmentContactDTO(
        id=item.id,
        department_id=item.department_id,
        campus_code=item.campus_code,
        contact_name=item.contact_name,
        office_name=item.office_name,
        phone=item.phone,
        email=item.email,
        location=item.location,
        office_hours=item.office_hours,
        valid_from=item.valid_from,
        valid_until=item.valid_until,
    )


class ServiceGuideService:
    def __init__(
        self,
        repository: GuideRepository,
        *,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self._repository = repository
        self._today_provider = today_provider

    async def search(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
        category_code: str | None = None,
        department_id: UUID | None = None,
        campus_code: str | None = None,
        student_type: str | None = None,
    ) -> GuidePageDTO:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("invalid pagination")
        if student_type is not None and student_type not in STUDENT_TYPES:
            raise ValueError("invalid student_type")
        result = await self._repository.search_published(
            GuideSearchQuery(
                page=page,
                page_size=page_size,
                q=q,
                category_code=category_code,
                department_id=department_id,
                campus_code=campus_code,
                student_type=student_type,
                as_of=self._today_provider(),
            )
        )
        return GuidePageDTO(
            items=tuple(_summary_dto(row) for row in result.rows),
            page=page,
            page_size=page_size,
            total=result.total,
            total_pages=ceil(result.total / page_size) if result.total else 0,
        )

    async def get_detail(
        self,
        guide_id: UUID,
        *,
        campus_code: str,
        student_type: str,
    ) -> GuideDetailDTO:
        if not campus_code or student_type not in STUDENT_TYPES:
            raise ValueError("invalid guide audience")
        result = await self._repository.get_published_detail(
            guide_id=guide_id,
            campus_code=campus_code,
            student_type=student_type,
            as_of=self._today_provider(),
        )
        if result is None:
            raise GuideNotFound()
        applicability = result.applicability
        return GuideDetailDTO(
            summary=_summary_dto(result),
            source_url=result.guide.source_url,
            applicability=GuideApplicabilityDTO(
                campus_code=applicability.campus_code,
                student_type=applicability.student_type,
                applicable=True,
                notes=applicability.notes,
            ),
            materials=tuple(
                GuideMaterialRawDTO(
                    id=item.id,
                    name=item.name,
                    description=item.description,
                    required=item.required,
                    copies=item.copies,
                    condition=dict(item.condition),
                    sort_order=item.sort_order,
                )
                for item in result.materials
            ),
            steps=tuple(
                GuideStepDTO(
                    step_no=item.step_no,
                    title=item.title,
                    description=item.description,
                    location=item.location,
                    estimated_minutes=item.estimated_minutes,
                )
                for item in result.steps
            ),
            contacts=tuple(_contact_dto(item) for item in result.contacts),
        )
