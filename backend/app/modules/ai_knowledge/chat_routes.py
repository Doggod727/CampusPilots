import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.deepseek import DeepSeekGateway
from app.modules.ai_knowledge.conversation_routes import conversation_data, message_data
from app.modules.ai_knowledge.citations import append_citations
from app.modules.ai_knowledge.conversations import ConversationRepository, ConversationService
from app.modules.ai_knowledge.knowledge import KnowledgeRepository, KnowledgeService
from app.modules.ai_knowledge.rag import RagChatService
from app.modules.ai_knowledge.retrieval import RetrievalService
from app.modules.ai_knowledge.vectors import BgeSmallZhEmbeddingProvider, ChromaVectorStore
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.shared.responses import SuccessResponse

router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID | None = None
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["rag", "learn"] = "rag"
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=10)

    @field_validator("knowledge_base_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value): raise ValueError("knowledge_base_ids must be unique")
        return value

    @model_validator(mode="after")
    def require_rag_scope(self):
        if self.mode == "rag" and not self.knowledge_base_ids:
            raise ValueError("RAG mode requires at least one knowledge base")
        return self


async def chat_dependency():
    settings = get_settings(); database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            import chromadb
            repository = ConversationRepository(session); conversations = ConversationService(session, repository)
            knowledge = KnowledgeService(session, KnowledgeRepository(session))
            vectors = ChromaVectorStore(chromadb.PersistentClient(path=str(settings.knowledge_chroma_path)))
            retrieval = RetrievalService(session, knowledge, BgeSmallZhEmbeddingProvider(str(settings.knowledge_embedding_model_path)), vectors, settings.knowledge_score_threshold)
            gateway = DeepSeekGateway(api_key=settings.deepseek_api_key.get_secret_value(), base_url=str(settings.deepseek_base_url), model=settings.deepseek_model)
            yield session, repository, conversations, RagChatService(session, conversations, retrieval, gateway, settings.knowledge_history_rounds)
    finally: await database.dispose()


@router.post("/api/v1/chat/completions", operation_id="createChatCompletion")
async def complete_chat(body: ChatRequest, request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)], state=Depends(chat_dependency)):
    session, repository, conversations, service = state
    async with session.begin():
        conversation = await repository.get_owned(body.conversation_id, user.user_id, lock=True) if body.conversation_id else await conversations.create(user.user_id)
        if conversation is None:
            from app.modules.ai_knowledge.conversations import ConversationNotFound
            raise ConversationNotFound()
        user_message, assistant, retrieval = await service.complete(user, conversation, body.question, body.knowledge_base_ids, request.state.request_id, mode=body.mode)
    citations = [{"citation_no": index + 1, "document_id": item.document_id, "document_title": item.document_title, "source_location": item.source_location, "page_number": item.page_number, "quote_excerpt": item.content[:500], "relevance_score": item.score} for index, item in enumerate(retrieval.citations)]
    return SuccessResponse(data={"conversation": conversation_data(conversation), "user_message": message_data(user_message), "assistant_message": message_data(assistant, citations)}, request_id=request.state.request_id, timestamp=datetime.now(UTC))


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str, separators=(',', ':'))}\n\n"


@router.post("/api/v1/chat/stream", operation_id="streamChatCompletion")
async def stream_chat(body: ChatRequest, request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)], state=Depends(chat_dependency)):
    session, repository, conversations, service = state

    async def events():
        conversation = await repository.get_owned(body.conversation_id, user.user_id, lock=True) if body.conversation_id else await conversations.create(user.user_id)
        if conversation is None:
            from app.modules.ai_knowledge.conversations import ConversationNotFound
            raise ConversationNotFound()
        user_message, assistant = await conversations.append_turn(conversation.id, user.user_id, body.question, request.state.request_id)
        result = await service.retrieve(user, body.question, body.knowledge_base_ids)
        if result.citations:
            append_citations(session, assistant.id, result.citations)
        await session.commit()
        yield sse("meta", {"conversation_id": conversation.id, "message_id": assistant.id, "request_id": request.state.request_id})
        citations = [{"citation_no": index + 1, "document_id": item.document_id, "document_title": item.document_title, "source_location": item.source_location, "page_number": item.page_number, "quote_excerpt": item.content[:500], "relevance_score": item.score} for index, item in enumerate(result.citations)]
        if not result.citations and not service.can_use_general_learning(body.mode, body.question, body.knowledge_base_ids):
            assistant.status, assistant.content, assistant.finish_reason = "fallback", "未在已授权且已发布的校园知识中找到可靠答案。", "fallback"
            assistant.completed_at = datetime.now(UTC)
            await session.commit()
            yield sse("sources", {"citations": []})
            yield sse("done", {"finish_reason": "fallback", "usage": {"prompt_tokens": 0, "completion_tokens": 0}})
            return
        if result.citations:
            source_text = [{"source": i + 1, "content": item.content[:1200], "title": item.document_title} for i, item in enumerate(result.citations)]
            messages = ({"role": "system", "content": "仅依据sources回答，不输出思维链。"}, {"role": "user", "content": json.dumps({"question": body.question, "sources": source_text}, ensure_ascii=False)})
            answer_prefix = ""
        else:
            messages = service.general_learning_messages(body.question)
            answer_prefix = service.GENERAL_LEARNING_LABEL + "\n\n"
        assistant.status = "streaming"; parts = []; sequence = 0
        try:
            async for delta in service.gateway.stream_text(messages):
                sequence += 1; parts.append(delta)
                yield sse("delta", {"sequence": sequence, "content": delta})
            conversations.complete(assistant, answer_prefix + "".join(parts))
            await session.commit()
            yield sse("sources", {"citations": citations})
            yield sse("done", {"finish_reason": "stop", "usage": {"prompt_tokens": 0, "completion_tokens": 0}})
        except Exception as exc:
            conversations.fail(assistant, getattr(exc, "code", "CHAT_STREAM_FAILED"))
            await session.commit()
            yield sse("error", {"code": assistant.error_code, "message": "回答生成失败", "retryable": False, "message_id": assistant.id})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
