from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campus_service.models import WorkOrder, WorkOrderEvent
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.modules.campus_service.work_order_schemas import work_order_data
from app.modules.campus_service.work_order_errors import CampusNotFound
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.shared.responses import SuccessResponse

SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._campuses = campuses
        self._work_orders = work_orders
        self._events = events
        self._idempotency = idempotency
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))

    async def create(
        self,
        *,
        actor: AuthenticatedUser,
        command: CreateWorkOrderCommand,
        idempotency_key: str,
        request_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
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
        async with self._session.begin():
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

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("Work-order clock must be timezone-aware.")
        return value.astimezone(UTC)

    @staticmethod
    def _actor_role(actor: AuthenticatedUser) -> str:
        role_codes = {role.code for role in actor.roles}
        if "student" in role_codes:
            return "student"
        return min(role_codes) if role_codes else "authenticated"
