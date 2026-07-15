import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.modules.ai_knowledge.tool_adapters import KnowledgeSearchToolHandler
from app.modules.agent_platform.tool_gateway.catalog import KnowledgeSearchInput


class Retrieval:
    async def search(self,*args): raise AssertionError("empty scope must not query")


def test_knowledge_tool_cannot_expand_empty_scope():
    invocation=SimpleNamespace(user=SimpleNamespace(user_id=uuid4(),permissions=("knowledge:read",)))
    result=asyncio.run(KnowledgeSearchToolHandler(Retrieval())(invocation,KnowledgeSearchInput(query="校历")))
    assert result.items==() and result.fallback_reason=="KNOWLEDGE_SCOPE_REQUIRED"
