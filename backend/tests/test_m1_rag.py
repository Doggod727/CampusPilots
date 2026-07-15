import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.modules.ai_knowledge.rag import RagChatService
from app.modules.ai_knowledge.retrieval import RetrievalResult


class Conversations:
    async def append_turn(self,*args): return SimpleNamespace(),SimpleNamespace(status="pending",content="",finish_reason=None,fallback_reason=None,completed_at=None,retrieval_confidence=None)
class Retrieval:
    async def search(self,*args): return RetrievalResult((),0.0)


def test_no_reliable_context_returns_fallback_without_provider_call():
    gateway=SimpleNamespace(json_completion=lambda *_: (_ for _ in ()).throw(AssertionError()))
    service=RagChatService(None,Conversations(),Retrieval(),gateway)
    _,assistant,_=asyncio.run(service.complete(SimpleNamespace(user_id=uuid4()),SimpleNamespace(id=uuid4()),"q",[uuid4()],"request-1"))
    assert assistant.status=="fallback" and assistant.finish_reason=="fallback"
