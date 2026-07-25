import hashlib
import json
from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from app.core.errors import AppError
from app.modules.campus_service.models import (
    ElectricityAccount,
    ElectricityAccountMember,
    ElectricityTopupRequest,
)
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    ElectricityRepository,
    resolve_enabled_campus_code,
)

MIN_TOPUP = Decimal("1.00")
MAX_TOPUP = Decimal("500.00")
TOPUP_CREDITED_NOTICE = "充值已到账，余额已更新"


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


class ElectricityCampusNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="TOOL_ARGUMENT_INVALID", message="校区不存在或已停用")


@dataclass(frozen=True)
class ElectricityBalance:
    room_id: UUID
    room_name: str
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
    source: str
    is_simulated: bool
    notice: str
    created_at: datetime
    replayed: bool = False
    request_hash: str = field(default="", repr=False)
    balance_after: Decimal | None = None


class ElectricityService:
    """M2 mock electricity application service, independent from M5 runtime types."""

    def __init__(
        self,
        repository: ElectricityRepository,
        *,
        campuses: CampusReferenceRepository | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._campuses = campuses
        self._now = now or (lambda: datetime.now(UTC))

    async def resolve_or_provision_account(
        self,
        *,
        user_id: UUID,
        campus: str,
        dormitory_area: str,
        building: str,
        room: str,
    ) -> ElectricityAccount:
        """按自然语言宿舍地址解析电费账户。

        电费数据为明确 Mock（is_simulated=true）：地址不存在对应账户时按地址
        确定性供应一个模拟账户，并将查询用户登记为住户，使后续余额查询、
        充值与工单创建沿用既有的成员关系校验。
        """

        if self._campuses is None:
            raise ElectricityArgumentInvalid()
        campus_code = await resolve_enabled_campus_code(self._campuses, campus)
        if campus_code is None:
            raise ElectricityCampusNotFound()
        location = {
            "campus_code": campus_code,
            "dormitory_area": dormitory_area.strip(),
            "building": building.strip(),
            "room": room.strip(),
        }
        account = await self._repository.get_account_by_location(**location)
        if account is None:
            account = ElectricityAccount(
                room_id=uuid4(),
                balance=self._simulated_balance(**location),
                currency="CNY",
                source="mock",
                is_simulated=True,
                **location,
            )
            self._repository.add_account(account)
        if await self._repository.get_member(account.room_id, user_id) is None:
            self._repository.add_member(
                ElectricityAccountMember(
                    room_id=account.room_id, user_id=user_id, member_role="resident"
                )
            )
        return account

    @staticmethod
    def _simulated_balance(
        *, campus_code: str, dormitory_area: str, building: str, room: str
    ) -> Decimal:
        digest = hashlib.sha256(
            f"{campus_code}|{dormitory_area}|{building}|{room}".encode("utf-8")
        ).hexdigest()
        cents = 2000 + int(digest[:8], 16) % 18000
        return (Decimal(cents) / 100).quantize(Decimal("0.01"))

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
            room_name=self._room_name(account),
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
        account = await self._authorized_account(user_id, room_ids, room_id)

        request_hash = self._request_hash(room_id, normalized_amount)
        existing = await self._repository.get_topup_for_update(user_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ElectricityIdempotencyConflict()
            return self._result(existing, replayed=True, balance_after=account.balance)

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
            notice=TOPUP_CREDITED_NOTICE,
            created_at=self._utc_now(),
        )
        # 充值立即入账：余额真实增加，后续查询可见；幂等重放不会重复入账。
        account.balance = (account.balance + normalized_amount).quantize(Decimal("0.01"))
        account.source_updated_at = self._utc_now()
        self._repository.add_topup(request)
        return self._result(request, replayed=False, balance_after=account.balance)

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
    def _room_name(account: ElectricityAccount) -> str:
        value = " · ".join(
            (account.dormitory_area, account.building, account.room)
        )
        return value if len(value) <= 100 else f"{value[:99]}…"

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("Electricity clock must be timezone-aware.")
        return value.astimezone(UTC)

    @staticmethod
    def _result(
        request: ElectricityTopupRequest,
        *,
        replayed: bool,
        balance_after: Decimal | None = None,
    ) -> ElectricityTopupResult:
        return ElectricityTopupResult(
            request_id=request.id,
            room_id=request.room_id,
            amount=request.amount,
            currency=request.currency.strip(),
            status=request.status,
            source="mock",
            is_simulated=request.is_simulated,
            notice=request.notice,
            created_at=request.created_at,
            replayed=replayed,
            request_hash=request.request_hash,
            balance_after=balance_after,
        )
