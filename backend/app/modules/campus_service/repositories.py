from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campus_service.models import (
    Campus,
    Department,
    DepartmentContact,
    ElectricityAccount,
    ElectricityAccountMember,
    ElectricityTopupRequest,
    GuideApplicability,
    GuideCategory,
    GuideMaterial,
    GuideStep,
    ServiceGuide,
    WorkOrder,
    WorkOrderEvent,
    WorkOrderRating,
)
from app.modules.campus_service.work_order_access import WorkOrderScope
from app.modules.campus_service.work_order_errors import WorkOrderNumberExhausted


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


@dataclass(frozen=True)
class GuideSearchQuery:
    page: int
    page_size: int
    q: str | None
    category_code: str | None
    department_id: UUID | None
    campus_code: str | None
    student_type: str | None
    as_of: date


@dataclass(frozen=True)
class GuideSearchRow:
    guide: ServiceGuide
    category: GuideCategory
    department: Department


@dataclass(frozen=True)
class GuideSearchPage:
    rows: tuple[GuideSearchRow, ...]
    total: int


@dataclass(frozen=True)
class GuideDetailRecord:
    guide: ServiceGuide
    category: GuideCategory
    department: Department
    applicability: GuideApplicability
    materials: tuple[GuideMaterial, ...]
    steps: tuple[GuideStep, ...]
    contacts: tuple[DepartmentContact, ...]


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


async def resolve_enabled_campus_code(
    campuses: CampusReferenceRepository, value: str
) -> str | None:
    """按编码（大小写不敏感）或名称（可省略"校区"后缀）解析启用校区的编码。"""

    text = value.strip()
    direct = await campuses.get_enabled_campus(text.lower())
    if direct is not None:
        return direct.code
    name = text.removesuffix("校区")
    for item in await campuses.list_enabled_campuses():
        if item.name == text or item.name.removesuffix("校区") == name:
            return item.code
    return None


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


class GuideRepository:
    """Published guide queries with audience and validity enforced in SQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _visibility_filters(as_of: date):
        return (
            ServiceGuide.status == "published",
            or_(ServiceGuide.valid_until.is_(None), ServiceGuide.valid_until >= as_of),
            GuideCategory.enabled.is_(True),
            Department.enabled.is_(True),
        )

    @staticmethod
    def _audience_exists(
        *, campus_code: str | None, student_type: str | None
    ):
        filters = [GuideApplicability.guide_id == ServiceGuide.id]
        if campus_code is not None:
            filters.append(GuideApplicability.campus_code == campus_code)
        if student_type is not None:
            filters.append(GuideApplicability.student_type.in_((student_type, "all")))
        return exists(select(1).where(*filters))

    async def search_published(self, query: GuideSearchQuery) -> GuideSearchPage:
        filters = [
            *self._visibility_filters(query.as_of),
            self._audience_exists(
                campus_code=query.campus_code,
                student_type=query.student_type,
            ),
        ]
        pattern = _literal_search_pattern(query.q)
        if pattern is not None:
            filters.append(
                or_(
                    ServiceGuide.title.ilike(pattern, escape="\\"),
                    ServiceGuide.summary.ilike(pattern, escape="\\"),
                )
            )
        if query.category_code is not None:
            filters.append(GuideCategory.code == query.category_code)
        if query.department_id is not None:
            filters.append(ServiceGuide.department_id == query.department_id)

        count_statement = (
            select(func.count(ServiceGuide.id))
            .join(GuideCategory, GuideCategory.id == ServiceGuide.category_id)
            .join(Department, Department.id == ServiceGuide.department_id)
            .where(*filters)
        )
        total = (await self._session.execute(count_statement)).scalar_one()

        statement = (
            select(ServiceGuide, GuideCategory, Department)
            .join(GuideCategory, GuideCategory.id == ServiceGuide.category_id)
            .join(Department, Department.id == ServiceGuide.department_id)
            .where(*filters)
            .order_by(
                GuideCategory.sort_order,
                ServiceGuide.updated_at.desc(),
                ServiceGuide.id,
            )
            .limit(query.page_size)
            .offset((query.page - 1) * query.page_size)
        )
        rows = tuple(
            GuideSearchRow(guide=guide, category=category, department=department)
            for guide, category, department in (await self._session.execute(statement)).all()
        )
        return GuideSearchPage(rows=rows, total=total)

    async def get_published_detail(
        self,
        *,
        guide_id: UUID,
        campus_code: str,
        student_type: str,
        as_of: date,
    ) -> GuideDetailRecord | None:
        statement = (
            select(ServiceGuide, GuideCategory, Department, GuideApplicability)
            .join(GuideCategory, GuideCategory.id == ServiceGuide.category_id)
            .join(Department, Department.id == ServiceGuide.department_id)
            .join(
                GuideApplicability,
                GuideApplicability.guide_id == ServiceGuide.id,
            )
            .where(
                ServiceGuide.id == guide_id,
                *self._visibility_filters(as_of),
                GuideApplicability.campus_code == campus_code,
                GuideApplicability.student_type.in_((student_type, "all")),
            )
            .order_by(
                case((GuideApplicability.student_type == student_type, 0), else_=1)
            )
            .limit(1)
        )
        row = (await self._session.execute(statement)).first()
        if row is None:
            return None
        guide, category, department, applicability = row

        material_statement = (
            select(GuideMaterial)
            .where(GuideMaterial.guide_id == guide.id)
            .order_by(GuideMaterial.sort_order, GuideMaterial.id)
        )
        materials = tuple(
            (await self._session.execute(material_statement)).scalars().all()
        )
        step_statement = (
            select(GuideStep)
            .where(GuideStep.guide_id == guide.id)
            .order_by(GuideStep.step_no, GuideStep.id)
        )
        steps = tuple((await self._session.execute(step_statement)).scalars().all())
        contact_statement = (
            select(DepartmentContact)
            .where(
                DepartmentContact.department_id == department.id,
                DepartmentContact.campus_code == campus_code,
                *_active_contact_filters(as_of),
            )
            .order_by(DepartmentContact.office_name, DepartmentContact.id)
        )
        contacts = tuple(
            (await self._session.execute(contact_statement)).scalars().all()
        )
        return GuideDetailRecord(
            guide=guide,
            category=category,
            department=department,
            applicability=applicability,
            materials=materials,
            steps=steps,
            contacts=contacts,
        )


class WorkOrderRepository:
    """Caller-owned-session persistence and locked public number allocation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, work_order: WorkOrder) -> None:
        self._session.add(work_order)

    @staticmethod
    def _visible(actor_user_id: UUID, scopes: tuple[WorkOrderScope, ...]):
        conditions = [WorkOrder.created_by == actor_user_id]
        conditions.extend(
            and_(
                WorkOrder.campus_code == scope.campus_code,
                WorkOrder.dormitory_area.in_(scope.dormitory_areas),
            )
            for scope in scopes
        )
        return or_(*conditions)

    async def list_visible(
        self,
        *,
        actor_user_id: UUID,
        scopes: tuple[WorkOrderScope, ...],
        page: int,
        page_size: int,
        status: str | None,
        campus_code: str | None,
        assigned_to_me: bool,
    ) -> tuple[tuple[tuple[WorkOrder, WorkOrderRating | None], ...], int]:
        filters = [self._visible(actor_user_id, scopes)]
        if status is not None:
            filters.append(WorkOrder.status == status)
        if campus_code is not None:
            filters.append(WorkOrder.campus_code == campus_code)
        if assigned_to_me:
            filters.append(WorkOrder.assigned_to == actor_user_id)
        total = int(
            (
                await self._session.execute(
                    select(func.count(WorkOrder.id)).where(*filters)
                )
            ).scalar_one()
        )
        statement = (
            select(WorkOrder, WorkOrderRating)
            .outerjoin(WorkOrderRating, WorkOrderRating.work_order_id == WorkOrder.id)
            .where(*filters)
            .order_by(WorkOrder.created_at.desc(), WorkOrder.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple((row[0], row[1]) for row in rows), total

    async def get_visible(
        self,
        work_order_id: UUID,
        *,
        actor_user_id: UUID,
        scopes: tuple[WorkOrderScope, ...],
    ) -> tuple[WorkOrder, WorkOrderRating | None] | None:
        statement = (
            select(WorkOrder, WorkOrderRating)
            .outerjoin(WorkOrderRating, WorkOrderRating.work_order_id == WorkOrder.id)
            .where(
                WorkOrder.id == work_order_id,
                self._visible(actor_user_id, scopes),
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        return None if row is None else (row[0], row[1])

    async def get_visible_for_update(
        self,
        work_order_id: UUID,
        *,
        actor_user_id: UUID,
        scopes: tuple[WorkOrderScope, ...],
    ) -> WorkOrder | None:
        statement = (
            select(WorkOrder)
            .where(
                WorkOrder.id == work_order_id,
                self._visible(actor_user_id, scopes),
            )
            .with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_owner_for_update(
        self, work_order_id: UUID, owner_user_id: UUID
    ) -> WorkOrder | None:
        statement = (
            select(WorkOrder)
            .where(
                WorkOrder.id == work_order_id,
                WorkOrder.created_by == owner_user_id,
            )
            .with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_rating(self, work_order_id: UUID) -> WorkOrderRating | None:
        statement = select(WorkOrderRating).where(
            WorkOrderRating.work_order_id == work_order_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add_rating(self, rating: WorkOrderRating) -> None:
        self._session.add(rating)

    async def allocate_order_no(self, issue_date: date) -> str:
        date_part = issue_date.strftime("%Y%m%d")
        lock_key = f"work_order_number:{date_part}"
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(lock_key)))
        )
        prefix = f"WO-{date_part}"
        statement = (
            select(WorkOrder.order_no)
            .where(WorkOrder.order_no.op("~")(rf"^{prefix}-[0-9]{{4}}$"))
            .order_by(WorkOrder.order_no.desc())
            .limit(1)
        )
        latest = (await self._session.execute(statement)).scalar_one_or_none()
        sequence = int(latest[-4:]) + 1 if latest is not None else 1
        if sequence > 9999:
            raise WorkOrderNumberExhausted()
        return f"{prefix}-{sequence:04d}"


class WorkOrderEventRepository:
    """Append-only work-order event persistence using caller-assigned sequences."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def append(self, event: WorkOrderEvent) -> None:
        self._session.add(event)

    async def next_sequence(self, work_order_id: UUID) -> int:
        statement = select(func.coalesce(func.max(WorkOrderEvent.sequence_no), 0) + 1).where(
            WorkOrderEvent.work_order_id == work_order_id
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def list_timeline(self, work_order_id: UUID) -> tuple[WorkOrderEvent, ...]:
        statement = (
            select(WorkOrderEvent)
            .where(WorkOrderEvent.work_order_id == work_order_id)
            .order_by(WorkOrderEvent.sequence_no, WorkOrderEvent.id)
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

    async def get_account_for_location(
        self,
        *,
        user_id: UUID,
        campus_code: str,
        dormitory_area: str,
        building: str,
        room: str,
    ) -> ElectricityAccount | None:
        statement = (
            select(ElectricityAccount)
            .join(
                ElectricityAccountMember,
                ElectricityAccountMember.room_id == ElectricityAccount.room_id,
            )
            .where(
                ElectricityAccountMember.user_id == user_id,
                ElectricityAccount.campus_code == campus_code,
                ElectricityAccount.dormitory_area == dormitory_area,
                ElectricityAccount.building == building,
                ElectricityAccount.room == room,
            )
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_account_by_location(
        self,
        *,
        campus_code: str,
        dormitory_area: str,
        building: str,
        room: str,
    ) -> ElectricityAccount | None:
        statement = select(ElectricityAccount).where(
            ElectricityAccount.campus_code == campus_code,
            ElectricityAccount.dormitory_area == dormitory_area,
            ElectricityAccount.building == building,
            ElectricityAccount.room == room,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_member(
        self, room_id: UUID, user_id: UUID
    ) -> ElectricityAccountMember | None:
        statement = select(ElectricityAccountMember).where(
            ElectricityAccountMember.room_id == room_id,
            ElectricityAccountMember.user_id == user_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add_account(self, account: ElectricityAccount) -> None:
        self._session.add(account)

    def add_member(self, member: ElectricityAccountMember) -> None:
        self._session.add(member)

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
