import asyncio
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.modules.agent_platform import composition
from app.modules.agent_platform.domain.contracts import ToolInvocationContext, UserContext
from app.modules.agent_platform.tool_gateway.catalog import (
    EventRegisterInput, EventSearchInput, LostFoundPublishInput,
)
from app.modules.agent_platform.tool_gateway.community_adapters import (
    EventRegisterToolHandler, EventSearchToolHandler, LostFoundPublishToolHandler,
)
from app.modules.agent_platform.tool_gateway.errors import ToolArgumentInvalid

USER = UUID(int=101)
EVENT = UUID(int=102)
NOW = datetime(2026, 7, 17, 8, tzinfo=UTC)


def invocation() -> ToolInvocationContext:
    return ToolInvocationContext(user=UserContext(user_id=USER, username="student01",
        permissions=("community:read", "community:write"), request_id="request-0001"),
        agent_run_id=UUID(int=103), step_id=UUID(int=104), idempotency_key="tool-key",
        arguments_hash="a" * 64, approval_id=UUID(int=105), approval_verified=True)


def test_event_search_rejects_unrepresentable_campus_filter() -> None:
    with pytest.raises(ToolArgumentInvalid):
        asyncio.run(EventSearchToolHandler(MagicMock())(
            invocation(), EventSearchInput(campus_id="main")))


def test_event_register_uses_transaction_entry_and_stable_uuid() -> None:
    service = MagicMock(); service.register = AsyncMock()
    output = asyncio.run(EventRegisterToolHandler(service)(invocation(), EventRegisterInput(event_id=EVENT)))
    assert output.status == "registered"
    assert service.register.await_args.kwargs["manage_transaction"] is False
    assert output.registration_id == asyncio.run(EventRegisterToolHandler(service)(
        invocation(), EventRegisterInput(event_id=EVENT))).registration_id


def test_lost_found_publish_maps_in_app_contact_without_schema_change() -> None:
    service = MagicMock(); service.create = AsyncMock(return_value=SimpleNamespace(
        body={"data": {"id": str(UUID(int=106)), "status": "published"}}))
    output = asyncio.run(LostFoundPublishToolHandler(service)(invocation(), LostFoundPublishInput(
        item_type="lost", title="学生卡", category="card", location="图书馆",
        occurred_at=NOW, description="黑色卡套内有学生卡", contact_preference="in_app")))
    assert output.status == "published"
    call = service.create.await_args.kwargs
    assert call["contact_type"] == "other" and call["contact_value"] == "站内联系：student01"
    assert call["manage_transaction"] is False


def test_only_runtime_composition_replaces_all_four_m3_mocks() -> None:
    source = inspect.getsource(composition.RuntimeCompositionFactory.build_tool_executor)
    expected = {"event.search": "EventSearchToolHandler", "event.register": "EventRegisterToolHandler",
                "lost_found.publish": "LostFoundPublishToolHandler",
                "lost_found.search_matches": "LostFoundMatchesToolHandler"}
    assert all(f'"{name}": {handler}' in source for name, handler in expected.items())
