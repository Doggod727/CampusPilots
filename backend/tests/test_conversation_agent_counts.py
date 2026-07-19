import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.ai_knowledge.conversation_routes import (
    conversation_fallback_titles,
    conversation_message_counts,
)


def test_agent_turns_are_counted_as_user_and_assistant_messages() -> None:
    conversation_id = uuid4()
    message_result = MagicMock()
    message_result.all.return_value = [(conversation_id, 3)]
    run_result = MagicMock()
    run_result.all.return_value = [(conversation_id, 2)]
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[message_result, run_result])

    counts = asyncio.run(conversation_message_counts(session, [conversation_id]))

    assert counts == {conversation_id: 7}
    assert session.execute.await_count == 2


def test_empty_conversation_page_skips_count_queries() -> None:
    session = MagicMock()
    session.execute = AsyncMock()

    assert asyncio.run(conversation_message_counts(session, [])) == {}
    session.execute.assert_not_awaited()


def test_old_default_title_uses_first_user_message_before_agent_input() -> None:
    conversation_id = uuid4()
    message = SimpleNamespace(conversation_id=conversation_id, content="第一条用户问题")
    run = SimpleNamespace(conversation_id=conversation_id, input_summary="Agent 请求")
    message_result = MagicMock()
    message_result.scalars.return_value.all.return_value = [message]
    run_result = MagicMock()
    run_result.scalars.return_value.all.return_value = [run]
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[message_result, run_result])
    conversation = SimpleNamespace(id=conversation_id, title="新对话")

    titles = asyncio.run(conversation_fallback_titles(session, [conversation]))

    assert titles == {conversation_id: "第一条用户问题"}
