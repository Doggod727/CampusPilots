import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.deepseek import DeepSeekUnavailable
from app.modules.ai_knowledge.rag import RagChatService
from app.modules.ai_knowledge.chat_routes import ChatRequest
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


def test_learning_without_sources_uses_general_model_and_never_fakes_citations():
    gateway=SimpleNamespace(json_completion=AsyncMock(return_value={"answer":"先理解导数定义。"}))
    service=RagChatService(None,Conversations(),Retrieval(),gateway)
    _,assistant,result=asyncio.run(service.complete(SimpleNamespace(user_id=uuid4()),SimpleNamespace(id=uuid4()),"讲解导数",[],"request-1",mode="learn"))
    assert assistant.status=="completed"
    assert assistant.content.startswith("通用模型回答，无校内资料引用")
    assert result.citations==()


def test_learning_campus_policy_question_without_sources_stays_rag_fallback():
    gateway=SimpleNamespace(json_completion=AsyncMock(side_effect=AssertionError("provider must not run")))
    service=RagChatService(None,Conversations(),Retrieval(),gateway)
    _,assistant,_=asyncio.run(service.complete(SimpleNamespace(user_id=uuid4()),SimpleNamespace(id=uuid4()),"本校奖学金政策是什么",[],"request-1",mode="learn"))
    assert assistant.status=="fallback"
    gateway.json_completion.assert_not_awaited()


def test_chat_contract_requires_sources_for_rag_but_allows_source_free_learning():
    with pytest.raises(Exception):
        ChatRequest(question="讲解导数",mode="rag",knowledge_base_ids=[])
    request=ChatRequest(question="讲解导数",mode="learn",knowledge_base_ids=[])
    assert request.mode=="learn" and request.knowledge_base_ids==[]
