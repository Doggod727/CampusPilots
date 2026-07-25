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
    CommunityPostPublishInput, CommunityTopicSummaryInput, EventCreateInput,
    EventRegisterInput, EventSearchInput, LostFoundPublishInput,
)
from app.modules.agent_platform.tool_gateway.community_adapters import (
    CommunityPostPublishToolHandler, CommunityTopicSummaryToolHandler,
    EventCreateToolHandler, EventRegisterToolHandler, EventSearchToolHandler,
    LostFoundPublishToolHandler,
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


def test_lost_found_publish_treats_naive_natural_time_as_campus_local_time() -> None:
    service = MagicMock(); service.create = AsyncMock(return_value=SimpleNamespace(
        body={"data": {"id": str(UUID(int=107)), "status": "published"}}))
    asyncio.run(LostFoundPublishToolHandler(service)(invocation(), LostFoundPublishInput(
        item_type="lost", title="小米手机", category="手机", location="二号体育场",
        occurred_at=datetime(2025, 4, 4, 15, 0), description="黑色手机带奶龙手机壳")))
    occurred_at = service.create.await_args.kwargs["occurred_at"]
    assert occurred_at.isoformat() == "2025-04-04T15:00:00+08:00"


def test_event_create_passes_approved_payload_into_real_service() -> None:
    service = MagicMock(); service.create = AsyncMock(return_value=SimpleNamespace(
        body={"data": {"id": str(UUID(int=108)), "status": "pending_review"}}))
    output = asyncio.run(EventCreateToolHandler(service)(invocation(), EventCreateInput(
        title="校园编程赛", description="面向全校学生的编程竞赛", category="competition",
        location="江安校区综合楼", starts_at=datetime(2027, 7, 20, 9),
        ends_at=datetime(2027, 7, 20, 18), registration_deadline=datetime(2027, 7, 18, 18),
        capacity=100,
    )))
    assert output.status == "pending_review"
    call = service.create.await_args.kwargs
    assert call["manage_transaction"] is False
    assert call["starts_at"].isoformat() == "2027-07-20T09:00:00+08:00"


def test_community_post_publish_resolves_topic_code_and_uses_approval() -> None:
    topic_id = UUID(int=109)
    topics = MagicMock(); topics.list = AsyncMock(return_value=SimpleNamespace(
        items=(SimpleNamespace(id=topic_id, code="mutual-help"),)))
    posts = MagicMock(); posts.create = AsyncMock(return_value=SimpleNamespace(
        body={"data": {"id": str(UUID(int=110)), "status": "published"}}))
    output = asyncio.run(CommunityPostPublishToolHandler(posts, topics)(
        invocation(), CommunityPostPublishInput(
            topic="mutual-help", title="求助选课", content="请问这门课程如何选择？",
        )))
    assert output.status == "published"
    call = posts.create.await_args.kwargs
    assert call["topic_id"] == topic_id and call["manage_transaction"] is False


def test_community_topic_summary_returns_current_post_digest() -> None:
    posts = MagicMock(); posts.list = AsyncMock(return_value=SimpleNamespace(
        total=2,
        items=(
            SimpleNamespace(id=UUID(int=111), topic=SimpleNamespace(name="校园生活"),
                title="食堂上新", content_markdown="二食堂新增窗口", like_count=5, comment_count=2),
            SimpleNamespace(id=UUID(int=112), topic=SimpleNamespace(name="互助问答"),
                title="选课提醒", content_markdown="今晚开放选课", like_count=3, comment_count=1),
        ),
    ))
    output = asyncio.run(CommunityTopicSummaryToolHandler(posts)(
        invocation(), CommunityTopicSummaryInput(limit=10)))
    assert output.total == 2
    assert "校园生活1条" in output.summary and "食堂上新" in output.summary


def test_runtime_composition_replaces_all_m3_mocks() -> None:
    source = inspect.getsource(composition.RuntimeCompositionFactory.build_tool_executor)
    expected = {"event.search": "EventSearchToolHandler", "event.register": "EventRegisterToolHandler",
                "lost_found.publish": "LostFoundPublishToolHandler",
                "lost_found.search_matches": "LostFoundMatchesToolHandler",
                "event.create": "EventCreateToolHandler",
                "community.post.publish": "CommunityPostPublishToolHandler",
                "community.topic.summarize": "CommunityTopicSummaryToolHandler"}
    assert all(f'"{name}": {handler}' in source for name, handler in expected.items())
