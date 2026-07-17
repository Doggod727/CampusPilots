import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.checkpointing import CheckpointCodec, DatabaseRuntimeCheckpointStore, InvalidRuntimeCheckpoint, PersistentRuntimeEventSink, RuntimeTerminalCoordinator
from app.modules.agent_platform.domain.contracts import AgentTask, RouteDecision, SupervisorPlan, ToolCallRequest, UserContext
from app.modules.agent_platform.models import AgentRuntimeCheckpoint
from app.modules.agent_platform.orchestration.runtime import RuntimeCheckpoint

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def state(*, pending: bool = False) -> RuntimeCheckpoint:
    run_id = uuid4(); step_id = uuid4()
    user = UserContext(user_id=uuid4(), username="student01", permissions=("electricity:topup",), request_id="request-123")
    route = RouteDecision(target_agent="service", confidence=Decimal("0.9"), source="rule", reason_code="ROUTE_RULE_SINGLE")
    task = AgentTask(task_id=uuid4(), agent_run_id=run_id, target_agent="service_agent", objective="充值", structured_input={"token": "secret"})
    plan = SupervisorPlan(status="ready", route=route, tasks=(task,), reason_code="SUPERVISOR_PLAN_READY")
    request = ToolCallRequest(agent_run_id=run_id, step_id=step_id, tool_name="electricity.create_topup_request", tool_version="1.0.0", arguments={"amount_cny": "10.00"}, idempotency_key="idem") if pending else None
    return RuntimeCheckpoint(user=user, objective="充值 10 元", context={"room": "A101"}, plan=plan, pending_step_id=step_id if pending else None, pending_request=request, pending_agent_code="service_agent" if pending else None)


def test_codec_encrypts_round_trips_and_rejects_tampering() -> None:
    codec = CheckpointCodec("dedicated-secret")
    original = state(pending=True)
    encrypted, digest = codec.encode(original)
    assert "充值" not in encrypted and "10.00" not in encrypted and "dedicated-secret" not in repr(codec)
    assert "充值" not in repr(original) and "10.00" not in repr(original)
    restored = codec.decode(encrypted, digest)
    assert restored.objective == original.objective and restored.pending_request == original.pending_request
    with pytest.raises(InvalidRuntimeCheckpoint):
        codec.decode(encrypted[:-2] + "aa", digest)


def test_database_store_creates_updates_with_cas_and_rejects_expired() -> None:
    repository = MagicMock(); repository.add = MagicMock(); repository.update_if_version = AsyncMock(return_value=True)
    repository.get = AsyncMock(return_value=None); repository.delete = AsyncMock(return_value=True)
    store = DatabaseRuntimeCheckpointStore(repository, CheckpointCodec("secret"), ttl=timedelta(minutes=5), clock=lambda: NOW)
    current = state(); asyncio.run(store.save(current.plan.tasks[0].agent_run_id, current))
    row = repository.add.call_args.args[0]
    assert isinstance(row, AgentRuntimeCheckpoint) and row.state_version == 1 and current.checkpoint_version == 1
    asyncio.run(store.save(current.plan.tasks[0].agent_run_id, current))
    repository.update_if_version.assert_awaited_once(); assert current.checkpoint_version == 2
    row.expires_at = NOW - timedelta(seconds=1); repository.get.return_value = row
    with pytest.raises(InvalidRuntimeCheckpoint): asyncio.run(store.load(row.run_id))


def test_database_store_rejects_failed_cas() -> None:
    repository = MagicMock(); repository.update_if_version = AsyncMock(return_value=False)
    store = DatabaseRuntimeCheckpointStore(repository, CheckpointCodec("secret"), ttl=timedelta(minutes=5), clock=lambda: NOW)
    current = state(); current.checkpoint_version = 2
    with pytest.raises(InvalidRuntimeCheckpoint): asyncio.run(store.save(current.plan.tasks[0].agent_run_id, current))


def test_persistent_event_sink_delegates_redacted_storage() -> None:
    repository = MagicMock(); expected = MagicMock(sequence=1); repository.append = AsyncMock(return_value=expected)
    sink = PersistentRuntimeEventSink(repository, request_id="request-123", clock=lambda: NOW)
    assert asyncio.run(sink.publish(uuid4(), "route", {"target": "service"})) is expected
    assert repository.append.await_args.kwargs["request_id"] == "request-123"


def test_terminal_coordinator_clears_checkpoint_before_appending_safe_event() -> None:
    checkpoints=MagicMock(); checkpoints.delete=AsyncMock(return_value=True)
    events=MagicMock(); events.append=AsyncMock()
    coordinator=RuntimeTerminalCoordinator(checkpoints,events,clock=lambda:NOW)
    asyncio.run(coordinator.complete(run_id=uuid4(),status="failed",request_id="request-123",error_code="SAFE_ERROR"))
    checkpoints.delete.assert_awaited_once()
    assert events.append.await_args.kwargs["event"]=="error"
    assert events.append.await_args.kwargs["data"]=={"status":"failed","error_code":"SAFE_ERROR"}
