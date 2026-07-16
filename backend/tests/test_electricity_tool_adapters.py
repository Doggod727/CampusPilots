import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import ToolCallRequest, UserContext
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.tool_gateway.electricity_adapters import (
    ElectricityBalanceToolHandler,
    ElectricityTopupToolHandler,
)
from app.modules.agent_platform.tool_gateway.executor import (
    AllowContentSafety,
    InMemoryApprovalVerifier,
    InMemoryAuditPort,
    ToolExecutor,
    canonical_arguments_hash,
)
from app.modules.agent_platform.tool_gateway.mocks import build_mock_handlers
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry
from app.modules.campus_service.electricity import ElectricityBalance, ElectricityTopupResult

USER_ID = UUID("10000000-0000-4000-8000-000000000001")
ROOM_ID = UUID("20000000-0000-4000-8000-000000000001")
RUN_ID = UUID("30000000-0000-4000-8000-000000000001")
STEP_ID = UUID("30000000-0000-4000-8000-000000000002")
APPROVAL_ID = UUID("40000000-0000-4000-8000-000000000001")
TOPUP_ID = UUID("50000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 15, tzinfo=UTC)


def _context() -> UserContext:
    return UserContext(
        user_id=USER_ID,
        username="student01",
        roles=("student",),
        permissions=("electricity:read_own", "electricity:topup_request:create"),
        request_id="request-electricity",
        room_ids=(ROOM_ID,),
    )


def _executor(service):
    handlers = build_mock_handlers()
    handlers["electricity.get_balance"] = ElectricityBalanceToolHandler(service)
    handlers["electricity.create_topup_request"] = ElectricityTopupToolHandler(service)
    verifier = InMemoryApprovalVerifier()
    return (
        ToolExecutor(
            registry=ToolRegistry(TOOL_CONTRACTS.values()),
            handlers=handlers,
            content_safety=AllowContentSafety(),
            approval_verifier=verifier,
            audit=InMemoryAuditPort(),
        ),
        verifier,
    )


def test_balance_adapter_maps_trusted_user_scope_and_frozen_output() -> None:
    service = MagicMock()
    service.get_balance = AsyncMock(return_value=ElectricityBalance(
        room_id=ROOM_ID, room_name="梅园 · 3号楼 · 301", balance=Decimal("42.50"), currency="CNY",
        source="mock", is_simulated=True, updated_at=NOW,
    ))
    executor, _ = _executor(service)
    result = asyncio.run(executor.execute(
        context=_context(),
        request=ToolCallRequest(
            agent_run_id=RUN_ID, step_id=STEP_ID, tool_name="electricity.get_balance",
            tool_version="1.0.0", arguments={"room_id": ROOM_ID},
        ),
        agent_allowlist=("electricity.get_balance",),
    ))
    assert result.data == {
        "room_id": str(ROOM_ID), "balance": "42.50", "currency": "CNY",
        "updated_at": "2026-07-15T00:00:00Z", "source": "mock", "is_simulated": True,
    }
    service.get_balance.assert_awaited_once_with(
        user_id=USER_ID, room_ids=(ROOM_ID,), room_id=ROOM_ID
    )


def test_topup_adapter_passes_verified_approval_and_idempotency_to_m2() -> None:
    service = MagicMock()
    service.create_topup_request = AsyncMock(return_value=ElectricityTopupResult(
        request_id=TOPUP_ID, room_id=ROOM_ID, amount=Decimal("20.00"),
        currency="CNY", status="simulated", is_simulated=True,
        source="mock", notice="internal wording", created_at=NOW, request_hash="c" * 64,
    ))
    executor, verifier = _executor(service)
    arguments = {"room_id": ROOM_ID, "amount": Decimal("20.00")}
    payload = TOOL_CONTRACTS["electricity.create_topup_request"].input_model.model_validate(arguments)
    verifier.grant(
        approval_id=APPROVAL_ID, user_id=USER_ID,
        tool_name="electricity.create_topup_request", tool_version="1.0.0",
        arguments_hash=canonical_arguments_hash(payload),
    )
    result = asyncio.run(executor.execute(
        context=_context(),
        request=ToolCallRequest(
            agent_run_id=RUN_ID, step_id=STEP_ID,
            tool_name="electricity.create_topup_request", tool_version="1.0.0",
            arguments=arguments, idempotency_key="idem-topup", approval_id=APPROVAL_ID,
        ),
        agent_allowlist=("electricity.create_topup_request",),
    ))
    assert result.data == {
        "topup_request_id": str(TOPUP_ID), "status": "simulated",
        "amount": "20.00", "notice": "模拟申请，不产生真实扣款或到账",
    }
    service.create_topup_request.assert_awaited_once_with(
        user_id=USER_ID, room_ids=(ROOM_ID,), room_id=ROOM_ID,
        amount=Decimal("20.00"), idempotency_key="idem-topup",
        agent_run_id=RUN_ID, approval_id=APPROVAL_ID, approval_verified=True,
    )


@pytest.mark.parametrize(
    ("idempotency_key", "approval_id", "expected_code"),
    [(None, None, "TOOL_ARGUMENT_INVALID"), ("idem", None, "TOOL_APPROVAL_REQUIRED")],
)
def test_invalid_topup_never_calls_real_adapter_service(
    idempotency_key, approval_id, expected_code
) -> None:
    service = MagicMock()
    service.create_topup_request = AsyncMock()
    executor, _ = _executor(service)
    with pytest.raises(AppError) as error:
        asyncio.run(executor.execute(
            context=_context(),
            request=ToolCallRequest(
                agent_run_id=RUN_ID, step_id=STEP_ID,
                tool_name="electricity.create_topup_request", tool_version="1.0.0",
                arguments={"room_id": ROOM_ID, "amount": Decimal("20.00")},
                idempotency_key=idempotency_key, approval_id=approval_id,
            ),
            agent_allowlist=("electricity.create_topup_request",),
        ))
    assert error.value.code == expected_code
    service.create_topup_request.assert_not_awaited()
