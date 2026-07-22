from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.agent_platform.models import AgentRun, AgentRunEvent
from app.modules.agent_platform.runtime_persistence import RuntimeEventRepository
from app.modules.agent_platform.traces import AgentRunNotFound
from app.modules.platform.audit import redact


class AgentEventCursorInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="AGENT_EVENT_CURSOR_INVALID", message="事件游标无效")


class AgentRunEventAccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_status(self, *, run_id: UUID, user_id: UUID, can_read_all: bool) -> str | None:
        stmt = select(AgentRun.status).where(AgentRun.id == run_id)
        if not can_read_all:
            stmt = stmt.where(AgentRun.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()


@dataclass(frozen=True)
class PreparedEventStream:
    run_id: UUID
    after_sequence: int
    request_id: str


class AgentRunEventStreamService:
    def __init__(self, *, access: AgentRunEventAccessRepository,
                 events: RuntimeEventRepository, poll_seconds: float = 1.0,
                 heartbeat_seconds: float = 15.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._access = access
        self._events = events
        self._poll_seconds = poll_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._sleep = sleep

    async def prepare(self, *, run_id: UUID, user_id: UUID, can_read_all: bool,
                      after_sequence: int, request_id: str) -> PreparedEventStream:
        if after_sequence < 0:
            raise AgentEventCursorInvalid()
        status = await self._access.get_status(run_id=run_id, user_id=user_id, can_read_all=can_read_all)
        if status is None:
            raise AgentRunNotFound()
        if after_sequence > await self._events.max_sequence(run_id):
            raise AgentEventCursorInvalid()
        return PreparedEventStream(run_id, after_sequence, request_id)

    async def iterate(self, prepared: PreparedEventStream) -> AsyncIterator[str]:
        cursor = prepared.after_sequence
        waited = 0.0
        try:
            while True:
                items = await self._events.replay(prepared.run_id, after_sequence=cursor)
                if items:
                    waited = 0.0
                    for item in items:
                        cursor = item.sequence
                        yield _encode_event(item, prepared.request_id)
                        if item.event in {"done", "error", "input_required"}:
                            return
                    continue
                await self._sleep(self._poll_seconds)
                waited += self._poll_seconds
                if waited >= self._heartbeat_seconds:
                    waited = 0.0
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            payload = json.dumps(
                {"run_id": str(prepared.run_id), "sequence": cursor + 1, "event": "error",
                 "occurred_at": datetime.now(UTC).isoformat(),
                 "data": {"code": "AGENT_EVENT_STREAM_FAILED", "message": "事件流已安全终止"}},
                ensure_ascii=False, separators=(",", ":"),
            )
            yield f"event: error\ndata: {payload}\n\n"


def _encode_event(item: AgentRunEvent, request_id: str) -> str:
    data: dict[str, Any] = {
        "run_id": str(item.run_id), "sequence": item.sequence,
        "event": item.event, "data": redact(item.data) or {},
        "occurred_at": item.occurred_at.isoformat(),
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {item.sequence}\nevent: {item.event}\ndata: {payload}\n\n"
