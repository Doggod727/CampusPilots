from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campus_service.models import (
    Campus,
    Department,
    DepartmentContact,
    ElectricityAccount,
    ElectricityAccountMember,
    ElectricityTopupRequest,
    GuideCategory,
)


@dataclass(frozen=True)
class DepartmentListQuery:
    q: str | None
    campus_code: str | None
    as_of: date


@dataclass(frozen=True)
class ContactListQuery:
    department_id: UUID | None
    campus_code: str | None
    as_of: date


def _active_contact_filters(as_of: date):
    return (
        DepartmentContact.enabled.is_(True),
        DepartmentContact.valid_from <= as_of,
        or_(
            DepartmentContact.valid_until.is_(None),
            DepartmentContact.valid_until >= as_of,
        ),
    )


def _literal_search_pattern(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    if not normalized:
        return None
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class CampusReferenceRepository:
    """Caller-owned-session lookups for enabled M2 reference dictionaries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_enabled_campuses(self) -> tuple[Campus, ...]:
        statement = (
            select(Campus)
            .where(Campus.enabled.is_(True))
            .order_by(Campus.sort_order, Campus.code)
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    async def get_enabled_campus(self, code: str) -> Campus | None:
        statement = select(Campus).where(Campus.code == code, Campus.enabled.is_(True))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_enabled_categories(self) -> tuple[GuideCategory, ...]:
        statement = (
            select(GuideCategory)
            .where(GuideCategory.enabled.is_(True))
            .order_by(GuideCategory.sort_order, GuideCategory.code)
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    async def get_enabled_category(self, code: str) -> GuideCategory | None:
        statement = select(GuideCategory).where(
            GuideCategory.code == code,
            GuideCategory.enabled.is_(True),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()


class DepartmentRepository:
    """Read-only department/contact persistence with validity filtering in SQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_departments(self, query: DepartmentListQuery) -> tuple[Department, ...]:
        statement = select(Department).where(Department.enabled.is_(True))
        pattern = _literal_search_pattern(query.q)
        if pattern is not None:
            statement = statement.where(
                or_(
                    Department.code.ilike(pattern, escape="\\"),
                    Department.name.ilike(pattern, escape="\\"),
                    Department.description.ilike(pattern, escape="\\"),
                )
            )
        if query.campus_code is not None:
            active_contact = exists(
                select(1).where(
                    DepartmentContact.department_id == Department.id,
                    DepartmentContact.campus_code == query.campus_code,
                    *_active_contact_filters(query.as_of),
                )
            )
            statement = statement.where(active_contact)
        statement = statement.order_by(Department.name, Department.code, Department.id)
        return tuple((await self._session.execute(statement)).scalars().all())

    async def get_department(self, department_id: UUID) -> Department | None:
        statement = select(Department).where(
            Department.id == department_id,
            Department.enabled.is_(True),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_active_contacts(
        self, query: ContactListQuery
    ) -> tuple[DepartmentContact, ...]:
        statement = select(DepartmentContact).where(*_active_contact_filters(query.as_of))
        if query.department_id is not None:
            statement = statement.where(
                DepartmentContact.department_id == query.department_id
            )
        if query.campus_code is not None:
            statement = statement.where(DepartmentContact.campus_code == query.campus_code)
        statement = statement.order_by(
            DepartmentContact.campus_code,
            DepartmentContact.office_name,
            DepartmentContact.id,
        )
        return tuple((await self._session.execute(statement)).scalars().all())


class ElectricityRepository:
    """Caller-owned-session persistence for mock electricity operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account_for_user(
        self, room_id: UUID, user_id: UUID
    ) -> ElectricityAccount | None:
        statement = (
            select(ElectricityAccount)
            .join(
                ElectricityAccountMember,
                ElectricityAccountMember.room_id == ElectricityAccount.room_id,
            )
            .where(
                ElectricityAccount.room_id == room_id,
                ElectricityAccountMember.user_id == user_id,
            )
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_room_ids_for_user(self, user_id: UUID) -> tuple[UUID, ...]:
        statement = (
            select(ElectricityAccountMember.room_id)
            .where(ElectricityAccountMember.user_id == user_id)
            .order_by(ElectricityAccountMember.room_id)
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    async def get_topup_for_update(
        self, requested_by: UUID, idempotency_key: str
    ) -> ElectricityTopupRequest | None:
        statement = (
            select(ElectricityTopupRequest)
            .where(
                ElectricityTopupRequest.requested_by == requested_by,
                ElectricityTopupRequest.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add_topup(self, request: ElectricityTopupRequest) -> None:
        self._session.add(request)
