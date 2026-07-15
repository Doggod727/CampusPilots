import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from app.core.errors import AppError
from app.modules.campus_service.models import ElectricityAccount, ElectricityTopupRequest
from app.modules.campus_service.repositories import ElectricityRepository

MIN_TOPUP = Decimal("1.00")
MAX_TOPUP = Decimal("500.00")
SIMULATION_NOTICE = "演示申请，不产生真实扣款或到账"


class ElectricityForbidden(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=403, code="TOOL_FORBIDDEN", message="无权访问该资源")


class ElectricityArgumentInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="TOOL_ARGUMENT_INVALID", message="电费工具参数无效")


class ElectricityIdempotencyConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="IDEMPOTENCY_CONFLICT", message="幂等键与已有请求冲突")


class ElectricityApprovalInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOOL_APPROVAL_INVALID", message="工具确认信息无效")


@dataclass(frozen=True)
class ElectricityBalance:
    room_id: UUID
    balance: Decimal
    currency: str
    source: str
    is_simulated: bool
    updated_at: datetime


@dataclass(frozen=True)
class ElectricityTopupResult:
    request_id: UUID
    room_id: UUID
    amount: Decimal
    currency: str
    status: str
    is_simulated: bool
    notice: str
    replayed: bool = False
    request_hash: str = field(default="", repr=False)


class ElectricityService:
    """M2 mock electricity application service, independent from M5 runtime types."""

    def __init__(self, repository: ElectricityRepository) -> None:
        self._repository = repository

    async def get_balance(
        self,
        *,
        user_id: UUID,
        room_ids: frozenset[UUID] | set[UUID] | tuple[UUID, ...],
        room_id: UUID,
    ) -> ElectricityBalance:
        account = await self._authorized_account(user_id, room_ids, room_id)
        return ElectricityBalance(
            room_id=account.room_id,
            balance=account.balance,
            currency=account.currency.strip(),
            source=account.source,
            is_simulated=account.is_simulated,
            updated_at=account.source_updated_at,
        )

    async def create_topup_request(
        self,
        *,
        user_id: UUID,
        room_ids: frozenset[UUID] | set[UUID] | tuple[UUID, ...],
        room_id: UUID,
        amount: Decimal,
        idempotency_key: str,
        agent_run_id: UUID | None = None,
        approval_id: UUID | None = None,
        approval_verified: bool = False,
    ) -> ElectricityTopupResult:
        normalized_amount = self._normalize_amount(amount)
        if not idempotency_key or len(idempotency_key) > 128:
            raise ElectricityArgumentInvalid()
        self._validate_approval(agent_run_id, approval_id, approval_verified)
        await self._authorized_account(user_id, room_ids, room_id)

        request_hash = self._request_hash(room_id, normalized_amount)
        existing = await self._repository.get_topup_for_update(user_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ElectricityIdempotencyConflict()
            return self._result(existing, replayed=True)

        request = ElectricityTopupRequest(
            id=uuid4(),
            room_id=room_id,
            requested_by=user_id,
            amount=normalized_amount,
            currency="CNY",
            status="simulated",
            is_simulated=True,
            agent_run_id=agent_run_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            notice=SIMULATION_NOTICE,
        )
        self._repository.add_topup(request)
        return self._result(request, replayed=False)

    async def _authorized_account(
        self,
        user_id: UUID,
        room_ids: frozenset[UUID] | set[UUID] | tuple[UUID, ...],
        room_id: UUID,
    ) -> ElectricityAccount:
        if room_id not in room_ids:
            raise ElectricityForbidden()
        account = await self._repository.get_account_for_user(room_id, user_id)
        if account is None:
            raise ElectricityForbidden()
        return account

    @staticmethod
    def _normalize_amount(amount: Decimal) -> Decimal:
        try:
            normalized = Decimal(amount).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            raise ElectricityArgumentInvalid() from None
        if not normalized.is_finite() or normalized < MIN_TOPUP or normalized > MAX_TOPUP:
            raise ElectricityArgumentInvalid()
        return normalized

    @staticmethod
    def _validate_approval(
        agent_run_id: UUID | None,
        approval_id: UUID | None,
        approval_verified: bool,
    ) -> None:
        paired = (agent_run_id is None) == (approval_id is None)
        direct_request = agent_run_id is None and approval_id is None
        if not paired or (direct_request and approval_verified) or (not direct_request and not approval_verified):
            raise ElectricityApprovalInvalid()

    @staticmethod
    def _request_hash(room_id: UUID, amount: Decimal) -> str:
        encoded = json.dumps(
            {"amount": format(amount, ".2f"), "room_id": str(room_id)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _result(request: ElectricityTopupRequest, *, replayed: bool) -> ElectricityTopupResult:
        return ElectricityTopupResult(
            request_id=request.id,
            room_id=request.room_id,
            amount=request.amount,
            currency=request.currency.strip(),
            status=request.status,
            is_simulated=request.is_simulated,
            notice=request.notice,
            replayed=replayed,
            request_hash=request.request_hash,
        )
