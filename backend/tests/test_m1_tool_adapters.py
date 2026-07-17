import asyncio
import inspect
from types import SimpleNamespace
from uuid import uuid4

from app.modules.ai_knowledge.tool_adapters import KnowledgeSearchToolHandler
from app.modules.agent_platform.tool_gateway.catalog import KnowledgeSearchInput
from app.modules.agent_platform import composition


class Retrieval:
    async def search(self,*args): raise AssertionError("empty scope must not query")


def test_knowledge_tool_cannot_expand_empty_scope():
    invocation=SimpleNamespace(user=SimpleNamespace(user_id=uuid4(),permissions=("knowledge:read",)))
    result=asyncio.run(KnowledgeSearchToolHandler(Retrieval())(invocation,KnowledgeSearchInput(query="校历")))
    assert result.items==() and result.fallback_reason=="KNOWLEDGE_SCOPE_REQUIRED"


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
