from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from app.modules.agent_platform.tool_gateway.catalog import (
    Citation,
    KnowledgeAnswerInput,
    KnowledgeAnswerOutput,
    KnowledgeSearchInput,
    KnowledgeSearchItem,
    KnowledgeSearchOutput,
)


def _knowledge_user(invocation):
    user = invocation.user
    return SimpleNamespace(user_id=user.user_id, permissions=user.permissions, department=None)


class KnowledgeSearchToolHandler:
    def __init__(self, retrieval) -> None: self.retrieval = retrieval

    async def __call__(self, invocation, payload):
        data = KnowledgeSearchInput.model_validate(payload)
        if not data.knowledge_base_ids:
            return KnowledgeSearchOutput(items=(), retrieval_version="m1-rag-v1", fallback_reason="KNOWLEDGE_SCOPE_REQUIRED")
        result = await self.retrieval.search(_knowledge_user(invocation), data.query, list(data.knowledge_base_ids), data.top_k)
        items = tuple(KnowledgeSearchItem(chunk_id=item.chunk_id, document_id=item.document_id, title=item.document_title, snippet=item.content[:1000], score=item.score, source_location=item.source_location, page_number=item.page_number) for item in result.citations)
        return KnowledgeSearchOutput(items=items, retrieval_version="m1-rag-v1", fallback_reason=None if items else "NO_RELIABLE_CONTEXT")


class KnowledgeAnswerToolHandler:
    def __init__(self, retrieval, gateway) -> None: self.retrieval, self.gateway = retrieval, gateway

    async def __call__(self, invocation, payload):
        data = KnowledgeAnswerInput.model_validate(payload)
        if not data.knowledge_base_ids:
            return KnowledgeAnswerOutput(answer="未提供可用的知识库范围。", citations=(), message_id=uuid4(), usage={"prompt_tokens": 0, "completion_tokens": 0}, finish_reason="fallback")
        result = await self.retrieval.search(_knowledge_user(invocation), data.question, list(data.knowledge_base_ids))
        if not result.citations:
            return KnowledgeAnswerOutput(answer="未在已授权且已发布的校园知识中找到可靠答案。", citations=(), message_id=uuid4(), usage={"prompt_tokens": 0, "completion_tokens": 0}, finish_reason="fallback")
        sources = [{"title": item.document_title, "content": item.content[:1200]} for item in result.citations]
        response = await self.gateway.json_completion(({"role": "system", "content": "仅依据sources回答，输出JSON对象answer，不输出思维链。"}, {"role": "user", "content": json.dumps({"question": data.question, "sources": sources}, ensure_ascii=False)}))
        answer = response.get("answer")
        if not isinstance(answer, str) or not answer.strip(): raise ValueError("invalid provider answer")
        citations = tuple(Citation(chunk_id=item.chunk_id, document_id=item.document_id, title=item.document_title, quote=item.content[:1000]) for item in result.citations)
        return KnowledgeAnswerOutput(answer=answer.strip(), citations=citations, message_id=uuid4(), usage={"prompt_tokens": 0, "completion_tokens": 0}, finish_reason="stop")
