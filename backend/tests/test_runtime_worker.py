import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.models import AgentRuntimeCommand
from app.modules.agent_platform.runtime_worker import GraphRuntimeCommandProcessor, OutboxRuntimeDispatcher, RuntimeWorker, TraceRuntimeFailureHandler
from app.modules.agent_platform.checkpointing import RuntimeStartPayloadCodec

NOW = datetime(2026, 7, 15, tzinfo=UTC); RUN = uuid4()


def test_outbox_dispatcher_encrypts_full_start_context() -> None:
    repository = MagicMock(); codec=RuntimeStartPayloadCodec("checkpoint-secret"); dispatcher = OutboxRuntimeDispatcher(repository, max_attempts=4, now=lambda: NOW,start_codec=codec)
    user = UserContext(user_id=uuid4(), username="student01", request_id="request-123")
    objective="课程问题"*800
    asyncio.run(dispatcher.start(RUN, user, objective, {"token": "***","mode":"auto"}))
    command = repository.add.call_args.args[0]
    assert command.action == "start" and command.payload["request_id"] == "request-123"
    assert objective not in str(command.payload)
    assert codec.decode(command.payload)==(objective,{"token":"***","mode":"auto"})


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


def test_graph_processor_restores_full_input_mode_and_context_from_outbox() -> None:
    runtime=MagicMock();runtime.start=AsyncMock();starts=MagicMock()
    user=UserContext(user_id=uuid4(),username="student01",request_id="request-123")
    starts.load=AsyncMock(return_value=(user,"truncated",{}));codec=RuntimeStartPayloadCodec("checkpoint-secret")
    objective="长输入"*1000;context={"_run_mode":"service","course":"高等数学"}
    command=AgentRuntimeCommand(run_id=RUN,action="start",payload=codec.encode(objective,context),status="processing")
    asyncio.run(GraphRuntimeCommandProcessor(runtime,starts,codec).process(command))
    runtime.start.assert_awaited_once_with(RUN,user,objective,context)


class Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


def test_worker_claims_with_fresh_sessions_and_marks_success() -> None:
    sessions = []
    @asynccontextmanager
    async def session_context():
        session = MagicMock(); session.begin.return_value = Tx(); session.execute=AsyncMock(return_value=MagicMock(rowcount=0)); sessions.append(session); yield session
    command = AgentRuntimeCommand(id=uuid4(), run_id=RUN, action="cancel", payload={}, status="processing", attempt_count=1, max_attempts=3)
    claim_repo = MagicMock(); claim_repo.claim_batch = AsyncMock(return_value=(command,))
    process_repo = MagicMock(); process_repo.get_processing = AsyncMock(return_value=command); process_repo.complete = AsyncMock(return_value=True)
    processor = MagicMock(); processor.process = AsyncMock()
    with patch("app.modules.agent_platform.runtime_worker.RuntimeCommandRepository", side_effect=[claim_repo, process_repo]):
        count = asyncio.run(RuntimeWorker(sessions=session_context, processor_factory=lambda _: processor, worker_id="worker-1", now=lambda: NOW).run_once())
    assert count == 1 and len(sessions) == 2; processor.process.assert_awaited_once_with(command)
    process_repo.complete.assert_awaited_once_with(command.id,"worker-1",NOW)


def test_worker_retries_then_marks_run_failed_without_leaking_exception() -> None:
    sessions = []
    @asynccontextmanager
    async def session_context():
        session = MagicMock(); session.begin.return_value = Tx(); session.execute=AsyncMock(return_value=MagicMock(rowcount=0)); sessions.append(session); yield session
    command = AgentRuntimeCommand(id=uuid4(), run_id=RUN, action="start", payload={}, status="processing", attempt_count=3, max_attempts=3)
    claim_repo = MagicMock(); claim_repo.claim_batch = AsyncMock(return_value=(command,))
    process_repo = MagicMock(); process_repo.get_processing = AsyncMock(return_value=command); process_repo.fail_or_retry = AsyncMock(return_value="failed")
    processor = MagicMock(); processor.process = AsyncMock(side_effect=RuntimeError("private failure")); failures = MagicMock(); failures.mark_failed = AsyncMock()
    with patch("app.modules.agent_platform.runtime_worker.RuntimeCommandRepository", side_effect=[claim_repo, process_repo]):
        asyncio.run(RuntimeWorker(sessions=session_context, processor_factory=lambda _: processor, worker_id="worker-1", now=lambda: NOW, failures=failures).run_once())
    assert process_repo.fail_or_retry.await_args.kwargs["error_code"] == "AGENT_RUNTIME_FAILED"
    assert process_repo.fail_or_retry.await_args.kwargs["worker_id"] == "worker-1"
    failures.mark_failed.assert_awaited_once_with(sessions[1], RUN, "AGENT_RUNTIME_FAILED")


def test_worker_survives_failure_accounting_errors_and_continues() -> None:
    @asynccontextmanager
    async def session_context():
        session = MagicMock(); session.begin.return_value = Tx(); session.execute=AsyncMock(return_value=MagicMock(rowcount=0)); yield session
    stuck = AgentRuntimeCommand(id=uuid4(), run_id=uuid4(), action="start", payload={}, status="processing", attempt_count=1, max_attempts=3)
    nxt = AgentRuntimeCommand(id=uuid4(), run_id=uuid4(), action="start", payload={}, status="processing", attempt_count=1, max_attempts=3)
    claim_repo = MagicMock(); claim_repo.claim_batch = AsyncMock(return_value=(stuck, nxt))
    broken_repo = MagicMock(); broken_repo.get_processing = AsyncMock(return_value=stuck); broken_repo.fail_or_retry = AsyncMock(side_effect=RuntimeError("connection invalid"))
    ok_repo = MagicMock(); ok_repo.get_processing = AsyncMock(return_value=nxt); ok_repo.fail_or_retry = AsyncMock(return_value="pending")
    processor = MagicMock(); processor.process = AsyncMock(side_effect=RuntimeError("provider down"))
    with patch("app.modules.agent_platform.runtime_worker.RuntimeCommandRepository", side_effect=[claim_repo, broken_repo, ok_repo]):
        count = asyncio.run(RuntimeWorker(sessions=session_context, processor_factory=lambda _: processor, worker_id="worker-1", now=lambda: NOW).run_once())
    assert count == 2 and ok_repo.fail_or_retry.await_count == 1


def test_worker_serve_uses_the_configured_single_command_batch() -> None:
    @asynccontextmanager
    async def session_context():
        yield MagicMock()
    worker=RuntimeWorker(sessions=session_context,processor_factory=lambda _:MagicMock(),worker_id="worker-1",batch_size=1)
    stop=asyncio.Event()
    async def run_once(*,limit): stop.set(); return 1
    worker.run_once=AsyncMock(side_effect=run_once)
    asyncio.run(worker.serve(stop))
    worker.run_once.assert_awaited_once_with(limit=1)


def test_permanent_delivery_failure_clears_checkpoint_and_emits_terminal_error() -> None:
    trace=MagicMock(); trace.finalize=AsyncMock()
    terminal=MagicMock(); terminal.complete=AsyncMock()
    with patch("app.modules.agent_platform.runtime_worker.TraceService",return_value=trace), patch(
        "app.modules.agent_platform.runtime_worker.RuntimeTerminalCoordinator",return_value=terminal
    ):
        asyncio.run(TraceRuntimeFailureHandler().mark_failed(MagicMock(),RUN,"SAFE_ERROR"))
    trace.finalize.assert_awaited_once_with(RUN,"failed",finish_reason="runtime_delivery_failed",error_code="SAFE_ERROR")
    terminal.complete.assert_awaited_once_with(run_id=RUN,status="failed",request_id=None,error_code="SAFE_ERROR")
