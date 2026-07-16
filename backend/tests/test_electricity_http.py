import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.campus_service.electricity import (
    ElectricityApprovalInvalid,
    ElectricityBalance,
    ElectricityTopupResult,
)
from app.modules.campus_service.electricity_http import ElectricityHttpService
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyDecision, IdempotencyReplay

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
ROOM_ID = UUID("21000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=USER_ID,
        username="student01",
        display_name="张同学",
        email=None,
        department=None,
        status="active",
        roles=(AuthenticatedRole(uuid4(), "student", "普通学生"),),
        permissions=("electricity:read_own", "electricity:topup_request:create"),
        last_login_at=None,
        created_at=NOW,
        version=1,
    )


def _service(*, decision=None):
    session = MagicMock()
    session.begin = MagicMock(return_value=_Transaction())
    session.flush = AsyncMock()
    repository = MagicMock()
    repository.list_room_ids_for_user = AsyncMock(return_value=(ROOM_ID,))
    electricity = MagicMock()
    electricity.get_balance = AsyncMock(
        return_value=ElectricityBalance(
            room_id=ROOM_ID,
            room_name="梅园 · 3号楼 · 301",
            balance=Decimal("42.50"),
            currency="CNY",
            source="mock",
            is_simulated=True,
            updated_at=NOW,
        )
    )
    electricity.create_topup_request = AsyncMock(
        return_value=ElectricityTopupResult(
            request_id=uuid4(),
            room_id=ROOM_ID,
            amount=Decimal("20.00"),
            currency="CNY",
            status="simulated",
            source="mock",
            is_simulated=True,
            notice="演示申请，不产生真实扣款或到账",
            created_at=NOW,
        )
    )
    idempotency = MagicMock()
    idempotency.begin = AsyncMock(
        return_value=decision or IdempotencyDecision(record_id=uuid4())
    )
    idempotency.complete = AsyncMock(return_value=True)
    audit = MagicMock()
    service = ElectricityHttpService(
        session=session,
        repository=repository,
        electricity=electricity,
        idempotency=idempotency,
        audit=audit,
        now=lambda: NOW,
    )
    return service, session, repository, electricity, idempotency, audit


def test_balance_loads_persisted_room_scope_before_domain_service() -> None:
    service, _, repository, electricity, _, _ = _service()
    result = asyncio.run(service.get_balance(actor=_actor(), room_id=ROOM_ID))

    assert result.room_name == "梅园 · 3号楼 · 301"
    repository.list_room_ids_for_user.assert_awaited_once_with(USER_ID)
    electricity.get_balance.assert_awaited_once_with(
        user_id=USER_ID,
        room_ids=(ROOM_ID,),
        room_id=ROOM_ID,
    )


def test_public_topup_persists_exact_idempotent_envelope_and_redacted_audit() -> None:
    service, session, repository, electricity, idempotency, audit = _service()
    result = asyncio.run(
        service.create_topup(
            actor=_actor(),
            room_id=ROOM_ID,
            amount=Decimal("20.00"),
            approval_id=None,
            agent_run_id=None,
            idempotency_key="topup-1",
            request_id="topup-request-1",
        )
    )

    assert result.status_code == 201
    assert result.body["data"]["source"] == "mock"
    assert result.body["data"]["amount_cny"] == "20.00"
    repository.list_room_ids_for_user.assert_awaited_once_with(USER_ID)
    electricity.create_topup_request.assert_awaited_once_with(
        user_id=USER_ID,
        room_ids=(ROOM_ID,),
        room_id=ROOM_ID,
        amount=Decimal("20.00"),
        idempotency_key="topup-1",
    )
    session.flush.assert_awaited_once()
    assert audit.record_success.call_args.kwargs["after_data"] == {
        "status": "simulated",
        "is_simulated": True,
    }
    assert str(ROOM_ID) not in str(audit.record_success.call_args.kwargs)
    assert idempotency.complete.await_args.kwargs["response_status"] == 201


def test_public_topup_replays_original_envelope() -> None:
    replay_body = {
        "code": "OK",
        "message": "success",
        "data": {"request_id": str(uuid4())},
        "request_id": "original-topup-request",
        "timestamp": NOW.isoformat(),
    }
    decision = IdempotencyDecision(
        record_id=uuid4(),
        replay=IdempotencyReplay(201, replay_body, "electricity_topup_request", None),
    )
    service, session, repository, electricity, _, audit = _service(decision=decision)
    result = asyncio.run(
        service.create_topup(
            actor=_actor(),
            room_id=ROOM_ID,
            amount=Decimal("20.00"),
            approval_id=None,
            agent_run_id=None,
            idempotency_key="topup-1",
            request_id="new-request",
        )
    )

    assert result.body == replay_body
    assert result.request_id == "original-topup-request"
    repository.list_room_ids_for_user.assert_not_awaited()
    electricity.create_topup_request.assert_not_awaited()
    session.flush.assert_not_awaited()
    audit.record_success.assert_not_called()


@pytest.mark.parametrize(
    ("approval_id", "agent_run_id"),
    [(uuid4(), None), (None, uuid4()), (uuid4(), uuid4())],
)
def test_public_topup_rejects_all_agent_confirmation_fields(
    approval_id: UUID | None,
    agent_run_id: UUID | None,
) -> None:
    service, _, repository, electricity, _, _ = _service()
    with pytest.raises(ElectricityApprovalInvalid):
        asyncio.run(
            service.create_topup(
                actor=_actor(),
                room_id=ROOM_ID,
                amount=Decimal("20.00"),
                approval_id=approval_id,
                agent_run_id=agent_run_id,
                idempotency_key="topup-agent",
                request_id="topup-request",
            )
        )
    repository.list_room_ids_for_user.assert_not_awaited()
    electricity.create_topup_request.assert_not_awaited()
