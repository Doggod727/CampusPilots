import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.internal_tools import InternalToolService, RunStepContext, ToolInvokeRequest
from app.modules.agent_platform.models import AgentRun, AgentStep

NOW = datetime(2026, 7, 17, tzinfo=UTC)
USER = uuid4()
RUN = uuid4()
STEP = uuid4()
HASH = "a" * 64


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def build_service(run_status: str, step_status: str):
    session = MagicMock()
    session.begin.return_value = Transaction()
    session.flush = AsyncMock()
    run = AgentRun(
        id=RUN, user_id=USER, client_request_id="req", input_summary="safe",
        status=run_status, step_count=1, specialist_count=1, created_at=NOW, updated_at=NOW,
    )
    step = AgentStep(
        id=STEP, run_id=RUN, sequence_no=1, agent_code="service_agent", task_type="generate",
        status=step_status, input_summary={}, output_summary={}, created_at=NOW,
    )
    repository = MagicMock()
    repository.get_run_step_for_update = AsyncMock(return_value=RunStepContext(run, step))
    repository.get_by_idempotency = AsyncMock(return_value=None)
    repository.add = MagicMock(side_effect=lambda entity: setattr(entity, "id", uuid4()))
    user_loader = MagicMock()
    user_loader.load = AsyncMock(
        return_value=UserContext(user_id=USER, username="student01", request_id="internal-req-0001")
    )
    electricity_repository = MagicMock()
    electricity_repository.list_room_ids_for_user = AsyncMock(return_value=[])
    prepared = SimpleNamespace(arguments_hash=HASH)
    executor = MagicMock()
    executor.prepare = MagicMock(return_value=prepared)
    executor.authorize = AsyncMock()
    executor.execute = AsyncMock(
        return_value=SimpleNamespace(status="succeeded", data={"balance": "88.50"}, duration_ms=3, audit_id=None)
    )
    agents = MagicMock()
    agents.get_active = MagicMock(
        return_value=SimpleNamespace(version=SimpleNamespace(tool_allowlist=("electricity.get_balance",)))
    )
    tools = MagicMock()
    tools.resolve = MagicMock(
        return_value=SimpleNamespace(definition=SimpleNamespace(version="1.0.0", requires_approval=False))
    )
    service = InternalToolService(
        session=session,
        repository=repository,
        user_loader=user_loader,
        electricity_repository=electricity_repository,
        executor=executor,
        approval_service=MagicMock(),
        agent_registry=agents,
        tool_registry=tools,
    )
    return service, run, step


def invoke(service):
    payload = ToolInvokeRequest(
        run_id=RUN, step_id=STEP, agent_code="service_agent", user_id=USER, arguments={"room_id": str(uuid4())}
    )
    return asyncio.run(
        service.invoke(
            tool_name="electricity.get_balance",
            payload=payload,
            idempotency_key="invoke-key-0001",
            request_id="internal-req-0001",
        )
    )


def test_invoke_keeps_awaiting_approval_run_and_step_status():
    service, run, step = build_service("awaiting_approval", "awaiting_approval")
    status, _data = invoke(service)
    assert status == 200
    assert run.status == "awaiting_approval"
    assert step.status == "awaiting_approval"


def test_invoke_marks_running_for_active_run_and_step():
    service, run, step = build_service("routing", "running")
    status, _data = invoke(service)
    assert status == 200
    assert run.status == "running"
    assert step.status == "running"
