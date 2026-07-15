import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.models import AgentRuntimeCommand
from app.modules.agent_platform.runtime_worker import GraphRuntimeCommandProcessor, OutboxRuntimeDispatcher, RuntimeWorker

NOW = datetime(2026, 7, 15, tzinfo=UTC); RUN = uuid4()


def test_outbox_dispatcher_stores_only_safe_command_metadata() -> None:
    repository = MagicMock(); dispatcher = OutboxRuntimeDispatcher(repository, max_attempts=4, now=lambda: NOW)
    user = UserContext(user_id=uuid4(), username="student01", request_id="request-123")
    asyncio.run(dispatcher.start(RUN, user, "private objective", {"token": "secret"}))
    command = repository.add.call_args.args[0]
    assert command.action == "start" and command.payload == {"request_id": "request-123"}
    assert "private objective" not in str(command.payload) and "secret" not in str(command.payload)


def test_best_effort_wakeup_never_breaks_committed_request() -> None:
    wakeup = MagicMock(); wakeup.notify = AsyncMock(side_effect=ConnectionError("redis down"))
    dispatcher = OutboxRuntimeDispatcher(MagicMock(), wakeup=wakeup)
    asyncio.run(dispatcher.notify_best_effort()); wakeup.notify.assert_awaited_once()


def test_graph_processor_loads_current_start_context_and_dispatches_actions() -> None:
    runtime = MagicMock(); runtime.start = AsyncMock(); runtime.resume = AsyncMock(); runtime.cancel = AsyncMock()
    starts = MagicMock(); user = UserContext(user_id=uuid4(), username="student01", request_id="request-123")
    starts.load = AsyncMock(return_value=(user, "safe summary", {})); processor = GraphRuntimeCommandProcessor(runtime, starts)
    command = AgentRuntimeCommand(run_id=RUN, action="start", payload={}, status="processing")
    asyncio.run(processor.process(command)); runtime.start.assert_awaited_once_with(RUN, user, "safe summary", {})


class Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


def test_worker_claims_with_fresh_sessions_and_marks_success() -> None:
    sessions = []
    @asynccontextmanager
    async def session_context():
        session = MagicMock(); session.begin.return_value = Tx(); sessions.append(session); yield session
    command = AgentRuntimeCommand(id=uuid4(), run_id=RUN, action="cancel", payload={}, status="processing", attempt_count=1, max_attempts=3)
    claim_repo = MagicMock(); claim_repo.claim_batch = AsyncMock(return_value=(command,))
    process_repo = MagicMock(); process_repo.get_processing = AsyncMock(return_value=command); process_repo.complete = AsyncMock(return_value=True)
    processor = MagicMock(); processor.process = AsyncMock()
    with patch("app.modules.agent_platform.runtime_worker.RuntimeCommandRepository", side_effect=[claim_repo, process_repo]):
        count = asyncio.run(RuntimeWorker(sessions=session_context, processor_factory=lambda _: processor, worker_id="worker-1", now=lambda: NOW).run_once())
    assert count == 1 and len(sessions) == 2; processor.process.assert_awaited_once_with(command); process_repo.complete.assert_awaited_once()


def test_worker_retries_then_marks_run_failed_without_leaking_exception() -> None:
    sessions = []
    @asynccontextmanager
    async def session_context():
        session = MagicMock(); session.begin.return_value = Tx(); sessions.append(session); yield session
    command = AgentRuntimeCommand(id=uuid4(), run_id=RUN, action="start", payload={}, status="processing", attempt_count=3, max_attempts=3)
    claim_repo = MagicMock(); claim_repo.claim_batch = AsyncMock(return_value=(command,))
    process_repo = MagicMock(); process_repo.get_processing = AsyncMock(return_value=command); process_repo.fail_or_retry = AsyncMock(return_value="failed")
    processor = MagicMock(); processor.process = AsyncMock(side_effect=RuntimeError("private failure")); failures = MagicMock(); failures.mark_failed = AsyncMock()
    with patch("app.modules.agent_platform.runtime_worker.RuntimeCommandRepository", side_effect=[claim_repo, process_repo]):
        asyncio.run(RuntimeWorker(sessions=session_context, processor_factory=lambda _: processor, worker_id="worker-1", now=lambda: NOW, failures=failures).run_once())
    assert process_repo.fail_or_retry.await_args.kwargs["error_code"] == "AGENT_RUNTIME_FAILED"
    failures.mark_failed.assert_awaited_once_with(sessions[1], RUN, "AGENT_RUNTIME_FAILED")
