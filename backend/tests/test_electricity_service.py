import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.modules.campus_service.electricity import (
    ElectricityApprovalInvalid,
    ElectricityArgumentInvalid,
    ElectricityCampusNotFound,
    ElectricityForbidden,
    ElectricityIdempotencyConflict,
    ElectricityService,
    TOPUP_CREDITED_NOTICE,
)
from app.modules.campus_service.models import ElectricityAccount, ElectricityTopupRequest

ROOM_ID = UUID("21000000-0000-4000-8000-000000000001")
OTHER_ROOM_ID = UUID("21000000-0000-4000-8000-000000000009")
USER_ID = UUID("21000000-0000-4000-8000-000000000002")
RUN_ID = UUID("21000000-0000-4000-8000-000000000003")
APPROVAL_ID = UUID("21000000-0000-4000-8000-000000000004")


def _repository(account: ElectricityAccount | None = None):
    repository = MagicMock()
    repository.get_account_for_user = AsyncMock(return_value=account)
    repository.get_topup_for_update = AsyncMock(return_value=None)
    return repository


def _account() -> ElectricityAccount:
    return ElectricityAccount(
        room_id=ROOM_ID,
        campus_code="main",
        dormitory_area="梅园",
        building="3号楼",
        room="301",
        balance=Decimal("88.50"),
        currency="CNY",
        source="mock",
        is_simulated=True,
        source_updated_at=datetime(2026, 7, 15, tzinfo=UTC),
    )


def test_balance_requires_context_scope_and_persisted_membership() -> None:
    repository = _repository(_account())
    service = ElectricityService(repository)
    balance = asyncio.run(
        service.get_balance(user_id=USER_ID, room_ids=frozenset({ROOM_ID}), room_id=ROOM_ID)
    )
    assert balance.balance == Decimal("88.50")
    assert balance.room_name == "梅园 · 3号楼 · 301"
    assert balance.currency == "CNY" and balance.source == "mock" and balance.is_simulated

    with pytest.raises(ElectricityForbidden) as outside_scope:
        asyncio.run(
            service.get_balance(
                user_id=USER_ID, room_ids=frozenset({ROOM_ID}), room_id=OTHER_ROOM_ID
            )
        )
    assert outside_scope.value.status_code == 403 and outside_scope.value.code == "TOOL_FORBIDDEN"
    repository.get_account_for_user.assert_awaited_once_with(ROOM_ID, USER_ID)

    repository.get_account_for_user.return_value = None
    with pytest.raises(ElectricityForbidden):
        asyncio.run(
            service.get_balance(user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID)
        )


def test_topup_credits_balance_immediately() -> None:
    account = _account()
    repository = _repository(account)
    service = ElectricityService(repository)
    result = asyncio.run(
        service.create_topup_request(
            user_id=USER_ID,
            room_ids={ROOM_ID},
            room_id=ROOM_ID,
            amount=Decimal("20"),
            idempotency_key="idem-1",
        )
    )
    request = repository.add_topup.call_args.args[0]
    assert result.amount == Decimal("20.00")
    assert result.currency == "CNY" and result.status == "simulated" and result.is_simulated
    assert result.notice == TOPUP_CREDITED_NOTICE and not result.replayed
    assert result.source == "mock" and result.created_at.tzinfo is not None
    assert request.request_hash and len(request.request_hash) == 64
    # 充值实时入账：余额从 88.50 增加到 108.50，结果携带充值后余额
    assert account.balance == Decimal("108.50")
    assert result.balance_after == Decimal("108.50")


def test_topup_replays_same_hash_and_rejects_different_hash() -> None:
    account = _account()
    repository = _repository(account)
    service = ElectricityService(repository)
    first = asyncio.run(
        service.create_topup_request(
            user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID,
            amount=Decimal("10.0"), idempotency_key="idem-1",
        )
    )
    assert account.balance == Decimal("98.50")
    stored: ElectricityTopupRequest = repository.add_topup.call_args.args[0]
    repository.get_topup_for_update.return_value = stored
    replay = asyncio.run(
        service.create_topup_request(
            user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID,
            amount=Decimal("10.00"), idempotency_key="idem-1",
        )
    )
    assert replay.request_id == first.request_id and replay.replayed
    repository.add_topup.assert_called_once()
    # 幂等重放不重复入账，余额保持首次充值后的数值
    assert account.balance == Decimal("98.50")
    assert replay.balance_after == Decimal("98.50")

    with pytest.raises(ElectricityIdempotencyConflict) as conflict:
        asyncio.run(
            service.create_topup_request(
                user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID,
                amount=Decimal("11"), idempotency_key="idem-1",
            )
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert stored.request_hash not in str(conflict.value)


@pytest.mark.parametrize("amount", [Decimal("0.99"), Decimal("500.01"), Decimal("NaN")])
def test_topup_rejects_invalid_amount(amount: Decimal) -> None:
    service = ElectricityService(_repository(_account()))
    with pytest.raises(ElectricityArgumentInvalid) as error:
        asyncio.run(
            service.create_topup_request(
                user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID,
                amount=amount, idempotency_key="idem-1",
            )
        )
    assert error.value.status_code == 422 and error.value.code == "TOOL_ARGUMENT_INVALID"


@pytest.mark.parametrize(
    ("run_id", "approval_id", "verified"),
    [(RUN_ID, None, True), (None, APPROVAL_ID, True), (RUN_ID, APPROVAL_ID, False), (None, None, True)],
)
def test_agent_topup_requires_paired_verified_approval(run_id, approval_id, verified) -> None:
    repository = _repository(_account())
    service = ElectricityService(repository)
    with pytest.raises(ElectricityApprovalInvalid) as error:
        asyncio.run(
            service.create_topup_request(
                user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID,
                amount=Decimal("20"), idempotency_key="idem-1",
                agent_run_id=run_id, approval_id=approval_id, approval_verified=verified,
            )
        )
    assert error.value.code == "TOOL_APPROVAL_INVALID"
    repository.add_topup.assert_not_called()


def test_resolve_or_provision_creates_simulated_account_and_membership() -> None:
    repository = _repository()
    repository.get_account_by_location = AsyncMock(return_value=None)
    repository.get_member = AsyncMock(return_value=None)
    campuses = MagicMock()
    campuses.get_enabled_campus = AsyncMock(return_value=None)
    campuses.list_enabled_campuses = AsyncMock(
        return_value=(SimpleNamespace(code="jiangan", name="江安校区"),)
    )
    service = ElectricityService(repository, campuses=campuses)
    account = asyncio.run(
        service.resolve_or_provision_account(
            user_id=USER_ID, campus="江安校区", dormitory_area="西苑",
            building="6舍3栋", room="601B",
        )
    )
    created = repository.add_account.call_args.args[0]
    assert created.campus_code == "jiangan"
    assert created.dormitory_area == "西苑" and created.building == "6舍3栋" and created.room == "601B"
    assert created.source == "mock" and created.is_simulated
    assert Decimal("20.00") <= created.balance <= Decimal("199.99")
    member = repository.add_member.call_args.args[0]
    assert member.room_id == account.room_id and member.user_id == USER_ID
    # 同一地址重复解析时余额稳定（确定性模拟余额）
    assert account.balance == ElectricityService._simulated_balance(
        campus_code="jiangan", dormitory_area="西苑", building="6舍3栋", room="601B"
    )


def test_resolve_or_provision_reuses_existing_account_and_skips_duplicate_member() -> None:
    existing = _account()
    repository = _repository()
    repository.get_account_by_location = AsyncMock(return_value=existing)
    repository.get_member = AsyncMock(return_value=SimpleNamespace(room_id=ROOM_ID, user_id=USER_ID))
    campuses = MagicMock()
    campuses.get_enabled_campus = AsyncMock(return_value=SimpleNamespace(code="main", name="主校区"))
    service = ElectricityService(repository, campuses=campuses)
    account = asyncio.run(
        service.resolve_or_provision_account(
            user_id=USER_ID, campus="MAIN", dormitory_area="梅园",
            building="3号楼", room="301",
        )
    )
    assert account is existing
    repository.add_account.assert_not_called()
    repository.add_member.assert_not_called()


def test_resolve_or_provision_rejects_unknown_campus() -> None:
    repository = _repository()
    campuses = MagicMock()
    campuses.get_enabled_campus = AsyncMock(return_value=None)
    campuses.list_enabled_campuses = AsyncMock(return_value=())
    service = ElectricityService(repository, campuses=campuses)
    with pytest.raises(ElectricityCampusNotFound) as error:
        asyncio.run(
            service.resolve_or_provision_account(
                user_id=USER_ID, campus="不存在校区", dormitory_area="西苑",
                building="6舍3栋", room="601B",
            )
        )
    assert error.value.code == "TOOL_ARGUMENT_INVALID"
    repository.add_account.assert_not_called()


def test_agent_topup_accepts_verified_pair_without_exposing_integrity_data() -> None:
    repository = _repository(_account())
    service = ElectricityService(repository)
    result = asyncio.run(
        service.create_topup_request(
            user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID,
            amount=Decimal("20"), idempotency_key="idem-agent",
            agent_run_id=RUN_ID, approval_id=APPROVAL_ID, approval_verified=True,
        )
    )
    request = repository.add_topup.call_args.args[0]
    assert request.agent_run_id == RUN_ID and request.approval_id == APPROVAL_ID
    assert request.request_hash not in repr(result)
    assert "idem-agent" not in repr(result)
