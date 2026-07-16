from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campus_service.electricity import (
    ElectricityApprovalInvalid,
    ElectricityBalance,
    ElectricityService,
)
from app.modules.campus_service.electricity_schemas import electricity_topup_data
from app.modules.campus_service.repositories import ElectricityRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.shared.responses import SuccessResponse


@dataclass(frozen=True)
class ElectricityMutationResult:
    status_code: int
    request_id: str
    body: dict[str, Any] = field(repr=False)


class ElectricityHttpService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ElectricityRepository,
        electricity: ElectricityService,
        idempotency: IdempotencyService,
        audit: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._electricity = electricity
        self._idempotency = idempotency
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))

    async def get_balance(
        self, *, actor: AuthenticatedUser, room_id: UUID
    ) -> ElectricityBalance:
        room_ids = await self._repository.list_room_ids_for_user(actor.user_id)
        return await self._electricity.get_balance(
            user_id=actor.user_id,
            room_ids=room_ids,
            room_id=room_id,
        )

    async def create_topup(
        self,
        *,
        actor: AuthenticatedUser,
        room_id: UUID,
        amount: Decimal,
        approval_id: UUID | None,
        agent_run_id: UUID | None,
        idempotency_key: str,
        request_id: str,
    ) -> ElectricityMutationResult:
        request_body = {
            "room_id": str(room_id),
            "amount_cny": format(amount, "f"),
            "approval_id": str(approval_id) if approval_id else None,
            "agent_run_id": str(agent_run_id) if agent_run_id else None,
        }
        async with self._session.begin():
            decision = await self._idempotency.begin(
                user_id=actor.user_id,
                endpoint="POST /api/v1/electricity/topup-requests",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                replay_body = dict(decision.replay.response_body)
                return ElectricityMutationResult(
                    decision.replay.response_status,
                    str(replay_body["request_id"]),
                    replay_body,
                )
            if decision.pending:
                raise IdempotencyConflict()
            if approval_id is not None or agent_run_id is not None:
                raise ElectricityApprovalInvalid()

            room_ids = await self._repository.list_room_ids_for_user(actor.user_id)
            result = await self._electricity.create_topup_request(
                user_id=actor.user_id,
                room_ids=room_ids,
                room_id=room_id,
                amount=amount,
                idempotency_key=idempotency_key,
            )
            await self._session.flush()
            response = SuccessResponse(
                data=electricity_topup_data(result),
                request_id=request_id,
                timestamp=self._utc_now(),
            ).model_dump(mode="json")
            self._audit.record_success(
                action="electricity.topup_request.create",
                resource_type="electricity_topup_request",
                resource_id=str(result.request_id),
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_username=actor.username,
                after_data={"status": "simulated", "is_simulated": True},
            )
            if not await self._idempotency.complete(
                record_id=decision.record_id,
                response_status=201,
                response_body=response,
                resource_type="electricity_topup_request",
                resource_id=str(result.request_id),
            ):
                raise IdempotencyConflict()
        return ElectricityMutationResult(201, request_id, response)

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("Electricity HTTP clock must be timezone-aware.")
        return value.astimezone(UTC)
