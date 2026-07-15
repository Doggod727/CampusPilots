import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.agent_platform.event_stream import AgentEventCursorInvalid, AgentRunEventStreamService, PreparedEventStream
from app.modules.agent_platform.models import AgentRunEvent
from app.modules.agent_platform.run_routes import get_event_stream_service
from app.modules.agent_platform.traces import AgentRunNotFound
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

NOW = datetime(2026, 7, 15, tzinfo=UTC); RUN = uuid4(); USER = uuid4()


def actor(perms=("agent:run:read_own",)):
    return AuthenticatedUser(USER,"student01","Student",None,None,"active",(AuthenticatedRole(uuid4(),"student","Student"),),tuple(perms),None,NOW,1)


def event(sequence=1,event_type="done"):
    return AgentRunEvent(id=uuid4(),run_id=RUN,sequence=sequence,event=event_type,data={"token":"raw","status":"succeeded"},request_id="runtime-request",occurred_at=NOW)


def test_prepare_enforces_ownership_and_cursor_boundary() -> None:
    access=MagicMock(); access.get_status=AsyncMock(return_value=None); events=MagicMock(); events.max_sequence=AsyncMock(return_value=2)
    service=AgentRunEventStreamService(access=access,events=events)
    with pytest.raises(AgentRunNotFound): asyncio.run(service.prepare(run_id=RUN,user_id=USER,can_read_all=False,after_sequence=0,request_id="request-123"))
    access.get_status.return_value="running"
    with pytest.raises(AgentEventCursorInvalid): asyncio.run(service.prepare(run_id=RUN,user_id=USER,can_read_all=False,after_sequence=3,request_id="request-123"))


def test_iterate_replays_redacted_events_and_closes_on_terminal() -> None:
    access=MagicMock(); events=MagicMock(); events.replay=AsyncMock(return_value=(event(),))
    service=AgentRunEventStreamService(access=access,events=events)
    async def collect(): return [item async for item in service.iterate(PreparedEventStream(RUN,0,"request-123"))]
    chunks=asyncio.run(collect()); payload="".join(chunks)
    assert "id: 1" in payload and "event: done" in payload and '"token":"***"' in payload and "raw" not in payload


def test_iterate_sends_heartbeat_before_later_terminal_event() -> None:
    events=MagicMock(); events.replay=AsyncMock(side_effect=[(),(event(),)])
    service=AgentRunEventStreamService(access=MagicMock(),events=events,poll_seconds=1,heartbeat_seconds=1,sleep=AsyncMock())
    async def collect(): return [item async for item in service.iterate(PreparedEventStream(RUN,0,"request-123"))]
    chunks=asyncio.run(collect()); assert chunks[0]==": keep-alive\n\n" and "event: done" in chunks[1]


def make_client(service, *, perms=("agent:run:read_own",)):
    app=create_app()
    async def auth(): return actor(perms)
    async def stream_service(): yield service
    app.dependency_overrides[get_authenticated_user]=auth
    app.dependency_overrides[get_event_stream_service]=stream_service
    return TestClient(app)


def test_stream_route_preserves_request_id_and_replays_last_event_id() -> None:
    service=MagicMock(); service.prepare=AsyncMock(return_value=PreparedEventStream(RUN,4,"stream-request"))
    async def iterate(_): yield "id: 5\nevent: done\ndata: {}\n\n"
    service.iterate=iterate
    response=make_client(service).get(f"/api/v1/agent-runs/{RUN}/stream",headers={"Last-Event-ID":"4","X-Request-Id":"stream-request"})
    assert response.status_code==200 and response.headers["X-Request-Id"]=="stream-request"
    assert response.headers["content-type"].startswith("text/event-stream") and "id: 5" in response.text
    assert service.prepare.await_args.kwargs["after_sequence"]==4


def test_stream_route_rejects_invalid_cursor_before_service_call() -> None:
    service=MagicMock(); service.prepare=AsyncMock()
    response=make_client(service).get(f"/api/v1/agent-runs/{RUN}/stream",headers={"Last-Event-ID":"future"})
    assert response.status_code==409 and response.json()["code"]=="AGENT_EVENT_CURSOR_INVALID"
    service.prepare.assert_not_awaited()
