import asyncio
import inspect
from types import SimpleNamespace
from uuid import uuid4

from app.modules.ai_knowledge.tool_adapters import KnowledgeSearchToolHandler
from app.modules.agent_platform.tool_gateway.catalog import KnowledgeSearchInput
from app.modules.agent_platform import composition


class Retrieval:
    def __init__(self, authorized=(), citations=()):
        self.authorized = authorized
        self.citations = citations
        self.searched_with = None
    async def authorized_knowledge_bases(self, user): return list(self.authorized)
    async def search(self, user, query, knowledge_base_ids, top_k=6):
        self.searched_with = (user, query, knowledge_base_ids, top_k)
        return SimpleNamespace(citations=self.citations, confidence=0.0)


def test_knowledge_tool_falls_back_when_user_has_no_authorized_base():
    retrieval = Retrieval(authorized=())
    invocation = SimpleNamespace(user=SimpleNamespace(user_id=uuid4(), permissions=("knowledge:read",)))
    result = asyncio.run(KnowledgeSearchToolHandler(retrieval)(invocation, KnowledgeSearchInput(query="校历")))
    assert result.items == () and result.fallback_reason == "KNOWLEDGE_SCOPE_REQUIRED"
    assert retrieval.searched_with is None


def test_knowledge_tool_resolves_empty_scope_to_authorized_bases_only():
    knowledge_base_id = uuid4()
    retrieval = Retrieval(authorized=(knowledge_base_id,))
    invocation = SimpleNamespace(user=SimpleNamespace(user_id=uuid4(), permissions=("knowledge:read",)))
    asyncio.run(KnowledgeSearchToolHandler(retrieval)(invocation, KnowledgeSearchInput(query="校历")))
    assert retrieval.searched_with[2] == [knowledge_base_id]


def test_runtime_composition_uses_real_m1_handlers_and_specialists():
    tool_source = inspect.getsource(composition.RuntimeCompositionFactory.build_tool_executor)
    runtime_source = inspect.getsource(composition.RuntimeCompositionFactory.build_graph_runtime)

    assert '"knowledge.search": KnowledgeSearchToolHandler' in tool_source
    assert '"knowledge.answer": KnowledgeAnswerToolHandler' in tool_source
    assert "DeepSeekSpecialistProvider(" in runtime_source
    assert "_tool_descriptors" in runtime_source
    assert '"knowledge_agent"' in runtime_source
    assert '"community_agent"' in runtime_source
    assert "DeterministicMockSpecialist" not in runtime_source
