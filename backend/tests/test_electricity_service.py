import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.modules.campus_service.electricity import (
    ElectricityApprovalInvalid,
    ElectricityArgumentInvalid,
    ElectricityForbidden,
    ElectricityIdempotencyConflict,
    ElectricityService,
    SIMULATION_NOTICE,
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


def test_topup_is_simulated_and_does_not_mutate_balance() -> None:
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
    assert result.notice == SIMULATION_NOTICE and not result.replayed
    assert result.source == "mock" and result.created_at.tzinfo is not None
    assert request.request_hash and len(request.request_hash) == 64
    assert account.balance == Decimal("88.50")


def test_topup_replays_same_hash_and_rejects_different_hash() -> None:
    repository = _repository(_account())
    service = ElectricityService(repository)
    first = asyncio.run(
        service.create_topup_request(
            user_id=USER_ID, room_ids={ROOM_ID}, room_id=ROOM_ID,
            amount=Decimal("10.0"), idempotency_key="idem-1",
        )
    )
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
