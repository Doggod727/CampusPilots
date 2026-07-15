from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.models import AgentRuntimeCommand
from app.modules.agent_platform.orchestration.runtime import BoundedGraphRuntime, RuntimeDispatcherPort
from app.modules.agent_platform.runtime_persistence import RuntimeCommandRepository
from app.modules.agent_platform.traces import AgentRunStateConflict, TraceRepository, TraceService


class RuntimeWakeupPort(Protocol):
    async def notify(self) -> None: ...


class RuntimeCommandProcessorPort(Protocol):
    async def process(self, command: AgentRuntimeCommand) -> None: ...


class RuntimeFailurePort(Protocol):
    async def mark_failed(self, session: object, run_id: UUID, error_code: str) -> None: ...


class RuntimeStartContextPort(Protocol):
    async def load(self, run_id: UUID) -> tuple[UserContext, str, dict]: ...


class OutboxRuntimeDispatcher(RuntimeDispatcherPort):
    """Adds safe command metadata to the caller's transaction."""

    def __init__(self, repository: RuntimeCommandRepository, *, max_attempts: int = 3,
                 now: Callable[[], datetime] | None = None,
                 wakeup: RuntimeWakeupPort | None = None) -> None:
        self._repository = repository
        self._max_attempts = max_attempts
        self._now = now or (lambda: datetime.now(UTC))
        self._wakeup = wakeup

    async def start(self, run_id: UUID, user: UserContext, objective: str, context) -> None:
        self._add(run_id, "start", payload={"request_id": user.request_id})

    async def resume(self, run_id: UUID, approval_id: UUID) -> None:
        self._add(run_id, "resume", approval_id=approval_id)

    async def cancel(self, run_id: UUID) -> None:
        self._add(run_id, "cancel")

    def _add(self, run_id: UUID, action: str, *, approval_id: UUID | None = None,
             payload: dict | None = None) -> None:
        now = self._now()
        self._repository.add(AgentRuntimeCommand(
            id=uuid4(), run_id=run_id, action=action, approval_id=approval_id,
            payload=payload or {}, status="pending", attempt_count=0,
            max_attempts=self._max_attempts, available_at=now,
            created_at=now, updated_at=now,
        ))

    async def notify_best_effort(self) -> None:
        if self._wakeup is None:
            return
        try:
            await self._wakeup.notify()
        except Exception:
            return


class GraphRuntimeCommandProcessor:
    def __init__(self, runtime: BoundedGraphRuntime, starts: RuntimeStartContextPort) -> None:
        self._runtime = runtime
        self._starts = starts

    async def process(self, command: AgentRuntimeCommand) -> None:
        if command.action == "start":
            user, objective, context = await self._starts.load(command.run_id)
            await self._runtime.start(command.run_id, user, objective, context)
        elif command.action == "resume" and command.approval_id is not None:
            await self._runtime.resume(command.run_id, command.approval_id)
        elif command.action == "cancel":
            try:
                await self._runtime.cancel(command.run_id)
            except AgentRunStateConflict:
                return
        else:
            raise ValueError("invalid runtime command")


class TraceRuntimeFailureHandler:
    async def mark_failed(self, session: object, run_id: UUID, error_code: str) -> None:
        try:
            await TraceService(TraceRepository(session)).finalize(
                run_id, "failed", finish_reason="runtime_delivery_failed", error_code=error_code,
            )
        except AgentRunStateConflict:
            return


SessionContextFactory = Callable[[], AbstractAsyncContextManager]
ProcessorFactory = Callable[[object], RuntimeCommandProcessorPort]


class RuntimeWorker:
    def __init__(self, *, sessions: SessionContextFactory, processor_factory: ProcessorFactory,
                 worker_id: str, claim_timeout: timedelta = timedelta(seconds=60),
                 poll_interval: float = 2.0, now: Callable[[], datetime] | None = None,
                 failures: RuntimeFailurePort | None = None) -> None:
        self._sessions = sessions
        self._processor_factory = processor_factory
        self._worker_id = worker_id
        self._claim_timeout = claim_timeout
        self._poll_interval = poll_interval
        self._now = now or (lambda: datetime.now(UTC))
        self._failures = failures

    async def run_once(self, *, limit: int = 10) -> int:
        async with self._sessions() as session:
            async with session.begin():
                claimed = await RuntimeCommandRepository(session).claim_batch(
                    worker_id=self._worker_id, now=self._now(),
                    stale_after=self._claim_timeout, limit=limit,
                )
                command_ids = tuple(item.id for item in claimed)
        for command_id in command_ids:
            await self._process_one(command_id)
        return len(command_ids)

    async def _process_one(self, command_id: UUID) -> None:
        async with self._sessions() as session:
            repository = RuntimeCommandRepository(session)
            async with session.begin():
                command = await repository.get_processing(command_id, self._worker_id)
                if command is None:
                    return
                try:
                    await self._processor_factory(session).process(command)
                except Exception as exc:
                    code = getattr(exc, "code", "AGENT_RUNTIME_FAILED")
                    retry_at = self._now() + timedelta(seconds=min(60, 2 ** max(command.attempt_count, 1)))
                    status = await repository.fail_or_retry(command.id, now=self._now(), retry_at=retry_at, error_code=code)
                    if status == "failed" and self._failures is not None:
                        await self._failures.mark_failed(session, command.run_id, code)
                else:
                    await repository.complete(command.id, self._now())

    async def serve(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            processed = await self.run_once()
            if processed == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    pass
