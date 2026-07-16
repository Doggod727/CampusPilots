from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.core.errors import AppError
from app.modules.campus_service.models import (
    Campus,
    Department,
    DepartmentContact,
    GuideCategory,
)
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    ContactListQuery,
    DepartmentListQuery,
    DepartmentRepository,
)


class DepartmentNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="DEPARTMENT_NOT_FOUND",
            message="部门不存在或已停用",
        )


@dataclass(frozen=True)
class CampusDTO:
    code: str
    name: str
    address: str | None
    sort_order: int


@dataclass(frozen=True)
class GuideCategoryDTO:
    id: UUID
    code: str
    name: str
    sort_order: int


@dataclass(frozen=True)
class DepartmentDTO:
    id: UUID
    code: str
    name: str
    description: str | None


@dataclass(frozen=True)
class DepartmentContactDTO:
    id: UUID
    department_id: UUID
    campus_code: str
    contact_name: str | None
    office_name: str
    phone: str | None
    email: str | None
    location: str
    office_hours: str | None
    valid_from: date
    valid_until: date | None


@dataclass(frozen=True)
class DepartmentDetailDTO:
    department: DepartmentDTO
    contacts: tuple[DepartmentContactDTO, ...]


def _campus_dto(item: Campus) -> CampusDTO:
    return CampusDTO(
        code=item.code,
        name=item.name,
        address=item.address,
        sort_order=item.sort_order,
    )


def _category_dto(item: GuideCategory) -> GuideCategoryDTO:
    return GuideCategoryDTO(
        id=item.id,
        code=item.code,
        name=item.name,
        sort_order=item.sort_order,
    )


def _department_dto(item: Department) -> DepartmentDTO:
    return DepartmentDTO(
        id=item.id,
        code=item.code,
        name=item.name,
        description=item.description,
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


class CampusReferenceService:
    def __init__(self, repository: CampusReferenceRepository) -> None:
        self._repository = repository

    async def list_campuses(self) -> tuple[CampusDTO, ...]:
        return tuple(
            _campus_dto(item) for item in await self._repository.list_enabled_campuses()
        )

    async def get_campus(self, code: str) -> CampusDTO | None:
        item = await self._repository.get_enabled_campus(code)
        return _campus_dto(item) if item is not None else None

    async def list_categories(self) -> tuple[GuideCategoryDTO, ...]:
        return tuple(
            _category_dto(item)
            for item in await self._repository.list_enabled_categories()
        )

    async def get_category(self, code: str) -> GuideCategoryDTO | None:
        item = await self._repository.get_enabled_category(code)
        return _category_dto(item) if item is not None else None


class DepartmentService:
    def __init__(
        self,
        repository: DepartmentRepository,
        *,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self._repository = repository
        self._today_provider = today_provider

    async def list_departments(
        self, *, q: str | None = None, campus_code: str | None = None
    ) -> tuple[DepartmentDTO, ...]:
        items = await self._repository.list_departments(
            DepartmentListQuery(
                q=q,
                campus_code=campus_code,
                as_of=self._today_provider(),
            )
        )
        return tuple(_department_dto(item) for item in items)

    async def get_department(
        self, department_id: UUID, *, campus_code: str | None = None
    ) -> DepartmentDetailDTO:
        department = await self._repository.get_department(department_id)
        if department is None:
            raise DepartmentNotFound()
        contacts = await self._repository.list_active_contacts(
            ContactListQuery(
                department_id=department_id,
                campus_code=campus_code,
                as_of=self._today_provider(),
            )
        )
        return DepartmentDetailDTO(
            department=_department_dto(department),
            contacts=tuple(_contact_dto(item) for item in contacts),
        )

    async def list_contacts(
        self,
        *,
        department_id: UUID | None = None,
        campus_code: str | None = None,
    ) -> tuple[DepartmentContactDTO, ...]:
        items = await self._repository.list_active_contacts(
            ContactListQuery(
                department_id=department_id,
                campus_code=campus_code,
                as_of=self._today_provider(),
            )
        )
        return tuple(_contact_dto(item) for item in items)
