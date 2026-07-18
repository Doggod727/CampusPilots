import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.deepseek import DeepSeekUnavailable
from app.modules.ai_knowledge.rag import RagChatService
from app.modules.ai_knowledge.retrieval import RetrievalCitation, RetrievalResult


class Conversations:
    async def append_turn(self,*args): return SimpleNamespace(),SimpleNamespace(id=uuid4(),status="pending",content="",finish_reason=None,fallback_reason=None,completed_at=None,retrieval_confidence=None)
class Retrieval:
    async def search(self,*args): return RetrievalResult((),0.0)


def test_no_reliable_context_returns_fallback_without_provider_call():
    gateway=SimpleNamespace(json_completion=lambda *_: (_ for _ in ()).throw(AssertionError()))
    service=RagChatService(None,Conversations(),Retrieval(),gateway)
    _,assistant,_=asyncio.run(service.complete(SimpleNamespace(user_id=uuid4()),SimpleNamespace(id=uuid4()),"q",[uuid4()],"request-1"))
    assert assistant.status=="fallback" and assistant.finish_reason=="fallback"


def test_invalid_provider_answer_maps_to_provider_error():
    citation = RetrievalCitation(chunk_id=uuid4(), document_id=uuid4(), document_title="t", content="c", source_location="loc", page_number=None, score=0.9)

    class Citations:
        async def search(self, *args): return RetrievalResult((citation,), 0.9)

    session = MagicMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])))
    gateway = SimpleNamespace(json_completion=AsyncMock(return_value={"response": "no answer key"}))
    service = RagChatService(session, Conversations(), Citations(), gateway)
    with pytest.raises(DeepSeekUnavailable):
        asyncio.run(service.complete(SimpleNamespace(user_id=uuid4()), SimpleNamespace(id=uuid4()), "q", [uuid4()], "request-1"))
