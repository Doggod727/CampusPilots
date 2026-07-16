from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campus_service.models import WorkOrder, WorkOrderEvent, WorkOrderRating
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    ElectricityRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.modules.campus_service.work_order_access import WorkOrderScopeRepository
from app.modules.campus_service.work_order_schemas import (
    PageMetaData,
    WorkOrderEventListData,
    WorkOrderPageData,
    work_order_data,
    work_order_event_data,
)
from app.modules.campus_service.work_order_errors import (
    CampusNotFound,
    ResourceVersionConflict,
    WorkOrderIllegalTransition,
    WorkOrderNotFound,
    WorkOrderAlreadyRated,
    WorkOrderNotCompleted,
    WorkOrderApprovalInvalid,
)
from app.modules.campus_service.work_order_state import WorkOrderStateMachine
from app.modules.platform.auth import PermissionDenied
from app.modules.platform.audit import AuditService
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.shared.responses import SuccessResponse

SHANGHAI = ZoneInfo("Asia/Shanghai")


class WorkOrderActor(Protocol):
    user_id: UUID
    username: str
    roles: tuple[object, ...]
    permissions: tuple[str, ...]


class _CallerTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@dataclass(frozen=True)
class CreateWorkOrderCommand:
    campus_code: str
    dormitory_area: str
    building: str
    room: str
    fault_category: str
    description: str = field(repr=False)
    preferred_start_at: datetime
    preferred_end_at: datetime


@dataclass(frozen=True)
class WorkOrderMutationResult:
    status_code: int
    request_id: str
    body: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class WorkOrderToolView:
    work_order: Any
    room_id: UUID | None
    events: tuple[Any, ...]


@dataclass(frozen=True)
class TransitionWorkOrderCommand:
    target_status: str
    reason: str = field(repr=False)
    completion_note: str | None = field(default=None, repr=False)
    version: int = 1


@dataclass(frozen=True)
class RateWorkOrderCommand:
    score: int
    comment: str | None = field(default=None, repr=False)


class WorkOrderService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        campuses: CampusReferenceRepository,
        work_orders: WorkOrderRepository,
        events: WorkOrderEventRepository,
        idempotency: IdempotencyService,
        audit: AuditService,
        scopes: WorkOrderScopeRepository | None = None,
        rooms: ElectricityRepository | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._campuses = campuses
        self._work_orders = work_orders
        self._events = events
        self._idempotency = idempotency
        self._audit = audit
        self._scopes = scopes
        self._rooms = rooms
        self._now = now or (lambda: datetime.now(UTC))

    async def create(
        self,
        *,
        actor: WorkOrderActor,
        command: CreateWorkOrderCommand,
        idempotency_key: str,
        request_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> WorkOrderMutationResult:
        return await self._create(
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            manage_transaction=True,
        )

    async def create_in_transaction(
        self,
        *,
        actor: WorkOrderActor,
        command: CreateWorkOrderCommand,
        idempotency_key: str,
        request_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> WorkOrderMutationResult:
        return await self._create(
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            manage_transaction=False,
        )

    async def create_from_room_in_transaction(
        self,
        *,
        actor: WorkOrderActor,
        room_ids: tuple[UUID, ...],
        room_id: UUID,
        fault_category: str,
        description: str,
        preferred_start_at: datetime,
        preferred_end_at: datetime,
        idempotency_key: str,
        request_id: str,
        agent_run_id: UUID,
        approval_id: UUID | None,
        approval_verified: bool,
    ) -> WorkOrderMutationResult:
        if approval_id is None or not approval_verified:
            raise WorkOrderApprovalInvalid()
        if self._rooms is None or room_id not in room_ids:
            from app.modules.campus_service.electricity import ElectricityForbidden
            raise ElectricityForbidden()
        account = await self._rooms.get_account_for_user(room_id, actor.user_id)
        if account is None:
            from app.modules.campus_service.electricity import ElectricityForbidden
            raise ElectricityForbidden()
        return await self.create_in_transaction(
            actor=actor,
            command=CreateWorkOrderCommand(
                campus_code=account.campus_code,
                dormitory_area=account.dormitory_area,
                building=account.building,
                room=account.room,
                fault_category=fault_category,
                description=description,
                preferred_start_at=preferred_start_at,
                preferred_end_at=preferred_end_at,
            ),
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    async def _create(
        self,
        *,
        actor: WorkOrderActor,
        command: CreateWorkOrderCommand,
        idempotency_key: str,
        request_id: str,
        ip_address: str | None,
        user_agent: str | None,
        manage_transaction: bool,
    ) -> WorkOrderMutationResult:
        request_body = {
            "campus_code": command.campus_code,
            "dormitory_area": command.dormitory_area,
            "building": command.building,
            "room": command.room,
            "fault_category": command.fault_category,
            "description": command.description,
            "preferred_start_at": command.preferred_start_at.isoformat(),
            "preferred_end_at": command.preferred_end_at.isoformat(),
        }
        transaction = self._session.begin() if manage_transaction else _CallerTransaction()
        async with transaction:
            decision = await self._idempotency.begin(
                user_id=actor.user_id,
                endpoint="POST /api/v1/work-orders",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                replay_body = dict(decision.replay.response_body)
                return WorkOrderMutationResult(
                    status_code=decision.replay.response_status,
                    request_id=str(replay_body["request_id"]),
                    body=replay_body,
                )
            if decision.pending:
                raise IdempotencyConflict()
            if await self._campuses.get_enabled_campus(command.campus_code) is None:
                raise CampusNotFound()

            now = self._utc_now()
            work_order_id = uuid4()
            order_no = await self._work_orders.allocate_order_no(
                now.astimezone(SHANGHAI).date()
            )
            work_order = WorkOrder(
                id=work_order_id,
                order_no=order_no,
                created_by=actor.user_id,
                campus_code=command.campus_code,
                dormitory_area=command.dormitory_area,
                building=command.building,
                room=command.room,
                fault_category=command.fault_category,
                description=command.description,
                preferred_start_at=command.preferred_start_at.astimezone(UTC),
                preferred_end_at=command.preferred_end_at.astimezone(UTC),
                status="submitted",
                assigned_to=None,
                assigned_department_id=None,
                rejection_reason=None,
                completion_note=None,
                version=1,
                submitted_at=now,
                accepted_at=None,
                processing_at=None,
                completed_at=None,
                cancelled_at=None,
                rejected_at=None,
                created_at=now,
                updated_at=now,
            )
            event = WorkOrderEvent(
                id=uuid4(),
                work_order_id=work_order_id,
                sequence_no=1,
                event_type="submitted",
                from_status=None,
                to_status="submitted",
                actor_user_id=actor.user_id,
                actor_role=self._actor_role(actor),
                reason=None,
                snapshot={
                    "work_order_id": str(work_order_id),
                    "status": "submitted",
                    "campus_code": command.campus_code,
                    "fault_category": command.fault_category,
                    "version": 1,
                },
                created_at=now,
            )
            self._work_orders.add(work_order)
            self._events.append(event)
            await self._session.flush()

            response = SuccessResponse(
                data=work_order_data(work_order),
                request_id=request_id,
                timestamp=now,
            ).model_dump(mode="json")
            self._audit.record_success(
                action="work_order.create",
                resource_type="work_order",
                resource_id=str(work_order_id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                ip_address=ip_address,
                user_agent=user_agent,
                after_data={
                    "status": "submitted",
                    "campus_code": command.campus_code,
                    "fault_category": command.fault_category,
                },
            )
            completed = await self._idempotency.complete(
                record_id=decision.record_id,
                response_status=201,
                response_body=response,
                resource_type="work_order",
                resource_id=str(work_order_id),
            )
            if not completed:
                raise IdempotencyConflict()
        return WorkOrderMutationResult(201, request_id, response)

    async def list_visible(
        self,
        *,
        actor: WorkOrderActor,
        page: int,
        page_size: int,
        status: str | None,
        campus_code: str | None,
        assigned_to_me: bool,
    ) -> WorkOrderPageData:
        scopes = await self._actor_scopes(actor.user_id)
        rows, total = await self._work_orders.list_visible(
            actor_user_id=actor.user_id,
            scopes=scopes,
            page=page,
            page_size=page_size,
            status=status,
            campus_code=campus_code,
            assigned_to_me=assigned_to_me,
        )
        return WorkOrderPageData(
            items=[work_order_data(item, rating) for item, rating in rows],
            pagination=PageMetaData(
                page=page,
                page_size=page_size,
                total=total,
                total_pages=(total + page_size - 1) // page_size,
            ),
        )

    async def get_visible(self, *, actor: WorkOrderActor, work_order_id: UUID):
        row = await self._work_orders.get_visible(
            work_order_id,
            actor_user_id=actor.user_id,
            scopes=await self._actor_scopes(actor.user_id),
        )
        if row is None:
            raise WorkOrderNotFound()
        return work_order_data(*row)

    async def list_events(
        self, *, actor: WorkOrderActor, work_order_id: UUID
    ) -> WorkOrderEventListData:
        row = await self._work_orders.get_visible(
            work_order_id,
            actor_user_id=actor.user_id,
            scopes=await self._actor_scopes(actor.user_id),
        )
        if row is None:
            raise WorkOrderNotFound()
        events = await self._events.list_timeline(work_order_id)
        return WorkOrderEventListData(items=[work_order_event_data(item) for item in events])

    async def get_tool_view(
        self,
        *,
        actor: WorkOrderActor,
        room_ids: tuple[UUID, ...],
        work_order_id: UUID,
    ) -> WorkOrderToolView:
        row = await self._work_orders.get_visible(
            work_order_id,
            actor_user_id=actor.user_id,
            scopes=await self._actor_scopes(actor.user_id),
        )
        if row is None:
            raise WorkOrderNotFound()
        work_order = work_order_data(*row)
        events = await self._events.list_timeline(work_order_id)
        account = None
        if self._rooms is not None:
            account = await self._rooms.get_account_for_location(
                user_id=actor.user_id,
                campus_code=work_order.campus_code,
                dormitory_area=work_order.dormitory_area,
                building=work_order.building,
                room=work_order.room,
            )
        room_id = account.room_id if account is not None and account.room_id in room_ids else None
        return WorkOrderToolView(
            work_order=work_order,
            room_id=room_id,
            events=tuple(work_order_event_data(item) for item in events),
        )

    async def transition(
        self,
        *,
        actor: WorkOrderActor,
        work_order_id: UUID,
        command: TransitionWorkOrderCommand,
        idempotency_key: str,
        request_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> WorkOrderMutationResult:
        request_body = {
            "work_order_id": str(work_order_id),
            "target_status": command.target_status,
            "reason": command.reason,
            "completion_note": command.completion_note,
            "version": command.version,
        }
        async with self._session.begin():
            decision = await self._idempotency.begin(
                user_id=actor.user_id,
                endpoint=f"POST /api/v1/work-orders/{work_order_id}/transitions",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                body = dict(decision.replay.response_body)
                return WorkOrderMutationResult(
                    decision.replay.response_status, str(body["request_id"]), body
                )
            if decision.pending:
                raise IdempotencyConflict()
            scopes = await self._actor_scopes(actor.user_id)
            work_order = await self._work_orders.get_visible_for_update(
                work_order_id,
                actor_user_id=actor.user_id,
                scopes=scopes,
            )
            if work_order is None:
                raise WorkOrderNotFound()

            owner_cancel = (
                work_order.created_by == actor.user_id
                and command.target_status == "cancelled"
            )
            in_staff_scope = any(
                scope.campus_code == work_order.campus_code
                and work_order.dormitory_area in scope.dormitory_areas
                for scope in scopes
            )
            if not owner_cancel:
                if "work_order:transition" not in actor.permissions:
                    raise PermissionDenied()
                if not in_staff_scope:
                    raise WorkOrderNotFound()
            if work_order.version != command.version:
                raise ResourceVersionConflict()

            previous_status = work_order.status
            now = self._utc_now()
            effects = WorkOrderStateMachine.apply(
                current_status=previous_status,
                target_status=command.target_status,
                reason=command.reason,
                completion_note=command.completion_note,
                now=now,
            )
            if command.target_status == "accepted":
                effects.updates["assigned_to"] = actor.user_id
            for key, value in effects.updates.items():
                setattr(work_order, key, value)
            work_order.version += 1
            sequence = await self._events.next_sequence(work_order_id)
            event = WorkOrderEvent(
                id=uuid4(),
                work_order_id=work_order_id,
                sequence_no=sequence,
                event_type=effects.event_type,
                from_status=previous_status,
                to_status=work_order.status,
                actor_user_id=actor.user_id,
                actor_role=self._actor_role(actor),
                reason=command.reason.strip(),
                snapshot={
                    "work_order_id": str(work_order_id),
                    "status": work_order.status,
                    "version": work_order.version,
                    "assigned_to": str(work_order.assigned_to) if work_order.assigned_to else None,
                },
                created_at=now,
            )
            self._events.append(event)
            await self._session.flush()
            body = SuccessResponse(
                data=work_order_data(work_order),
                request_id=request_id,
                timestamp=now,
            ).model_dump(mode="json")
            self._audit.record_success(
                action="work_order.transition",
                resource_type="work_order",
                resource_id=str(work_order_id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                ip_address=ip_address,
                user_agent=user_agent,
                before_data={"status": previous_status, "version": command.version},
                after_data={
                    "status": work_order.status,
                    "version": work_order.version,
                    "has_reason": bool(command.reason.strip()),
                    "has_completion_note": bool(command.completion_note and command.completion_note.strip()),
                },
            )
            completed = await self._idempotency.complete(
                record_id=decision.record_id,
                response_status=200,
                response_body=body,
                resource_type="work_order",
                resource_id=str(work_order_id),
            )
            if not completed:
                raise IdempotencyConflict()
        return WorkOrderMutationResult(200, request_id, body)

    async def rate(
        self,
        *,
        actor: WorkOrderActor,
        work_order_id: UUID,
        command: RateWorkOrderCommand,
        idempotency_key: str,
        request_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> WorkOrderMutationResult:
        request_body = {
            "work_order_id": str(work_order_id),
            "score": command.score,
            "comment": command.comment,
        }
        async with self._session.begin():
            decision = await self._idempotency.begin(
                user_id=actor.user_id,
                endpoint=f"POST /api/v1/work-orders/{work_order_id}/rating",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                body = dict(decision.replay.response_body)
                return WorkOrderMutationResult(
                    decision.replay.response_status, str(body["request_id"]), body
                )
            if decision.pending:
                raise IdempotencyConflict()
            work_order = await self._work_orders.get_owner_for_update(
                work_order_id, actor.user_id
            )
            if work_order is None:
                raise WorkOrderNotFound()
            if work_order.status != "completed":
                raise WorkOrderNotCompleted()
            if await self._work_orders.get_rating(work_order_id) is not None:
                raise WorkOrderAlreadyRated()

            now = self._utc_now()
            rating = WorkOrderRating(
                id=uuid4(),
                work_order_id=work_order_id,
                user_id=actor.user_id,
                score=command.score,
                comment=command.comment.strip() if command.comment else None,
                created_at=now,
            )
            self._work_orders.add_rating(rating)
            await self._session.flush()
            rating_data = {
                "id": rating.id,
                "score": rating.score,
                "comment": rating.comment,
                "created_at": rating.created_at,
            }
            body = SuccessResponse(
                data=rating_data,
                request_id=request_id,
                timestamp=now,
            ).model_dump(mode="json")
            self._audit.record_success(
                action="work_order.rate",
                resource_type="work_order",
                resource_id=str(work_order_id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                ip_address=ip_address,
                user_agent=user_agent,
                after_data={
                    "score": command.score,
                    "has_comment": bool(command.comment and command.comment.strip()),
                },
            )
            completed = await self._idempotency.complete(
                record_id=decision.record_id,
                response_status=201,
                response_body=body,
                resource_type="work_order_rating",
                resource_id=str(rating.id),
            )
            if not completed:
                raise IdempotencyConflict()
        return WorkOrderMutationResult(201, request_id, body)

    async def _actor_scopes(self, actor_user_id: UUID):
        return () if self._scopes is None else await self._scopes.get_for_user(actor_user_id)

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("Work-order clock must be timezone-aware.")
        return value.astimezone(UTC)

    @staticmethod
    def _actor_role(actor: WorkOrderActor) -> str:
        role_codes = {
            role.code if hasattr(role, "code") else str(role)
            for role in actor.roles
        }
        if "student" in role_codes:
            return "student"
        return min(role_codes) if role_codes else "authenticated"
