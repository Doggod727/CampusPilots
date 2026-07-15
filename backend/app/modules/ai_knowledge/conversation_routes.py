from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.ai_knowledge.conversations import ConversationNotFound, ConversationRepository, ConversationService
from app.modules.ai_knowledge.models import Conversation, Message, MessageCitation, MessageFeedback
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.shared.responses import SuccessResponse

router = APIRouter(tags=["Conversations"])


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="新对话", min_length=1, max_length=100)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal[-1, 1]
    correction: str | None = Field(default=None, max_length=1000)


async def dependency():
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            repository = ConversationRepository(session)
            yield session, repository, ConversationService(session, repository)
    finally:
        await database.dispose()


def conversation_data(entity: Conversation) -> dict:
    return {key: getattr(entity, key) for key in ("id", "title", "status", "last_message_at", "created_at", "updated_at")}


def message_data(entity: Message, citations=()) -> dict:
    return {"id": entity.id, "conversation_id": entity.conversation_id, "sequence_no": entity.sequence_no, "role": entity.role, "status": entity.status, "content": entity.content, "finish_reason": entity.finish_reason, "usage": {"prompt_tokens": entity.prompt_tokens or 0, "completion_tokens": entity.completion_tokens or 0}, "citations": list(citations), "created_at": entity.created_at, "completed_at": entity.completed_at}


@router.get("/api/v1/conversations", operation_id="listConversations")
async def list_conversations(request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], state=Depends(dependency), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    session = state[0]
    filters = (Conversation.user_id == user.user_id, Conversation.deleted_at.is_(None))
    rows = (await session.execute(select(Conversation).where(*filters).order_by(Conversation.updated_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    total = await session.scalar(select(func.count()).select_from(Conversation).where(*filters))
    return SuccessResponse(data={"items": [conversation_data(row) for row in rows], "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": (total + page_size - 1) // page_size}}, request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.post("/api/v1/conversations", operation_id="createConversation", status_code=201)
async def create_conversation(body: ConversationCreate, request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], state=Depends(dependency)):
    async with state[0].begin(): entity = await state[2].create(user.user_id, body.title)
    return SuccessResponse(data=conversation_data(entity), request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.get("/api/v1/conversations/{conversation_id}", operation_id="getConversation")
async def get_conversation(conversation_id: UUID, request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], state=Depends(dependency)):
    entity = await state[1].get_owned(conversation_id, user.user_id)
    if entity is None: raise ConversationNotFound()
    return SuccessResponse(data=conversation_data(entity), request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.delete("/api/v1/conversations/{conversation_id}", operation_id="deleteConversation", status_code=204)
async def delete_conversation(conversation_id: UUID, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], state=Depends(dependency)):
    entity = await state[1].get_owned(conversation_id, user.user_id, lock=True)
    if entity is None: raise ConversationNotFound()
    entity.status, entity.deleted_at = "deleted", datetime.now(UTC)
    return Response(status_code=204)


async def owned_message(session, message_id: UUID, user_id: UUID):
    entity = (await session.execute(select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(Message.id == message_id, Conversation.user_id == user_id, Conversation.deleted_at.is_(None)))).scalar_one_or_none()
    if entity is None: raise ConversationNotFound()
    return entity


@router.get("/api/v1/conversations/{conversation_id}/messages", operation_id="listConversationMessages")
async def list_messages(conversation_id: UUID, request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], state=Depends(dependency), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    if await state[1].get_owned(conversation_id, user.user_id) is None: raise ConversationNotFound()
    rows = (await state[0].execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.sequence_no).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return SuccessResponse(data={"items": [message_data(row) for row in rows], "pagination": {"page": page, "page_size": page_size, "total": len(rows), "total_pages": 1 if rows else 0}}, request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.get("/api/v1/messages/{message_id}", operation_id="getMessage")
async def get_message(message_id: UUID, request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], state=Depends(dependency)):
    entity = await owned_message(state[0], message_id, user.user_id)
    citations = (await state[0].execute(select(MessageCitation).where(MessageCitation.message_id == message_id).order_by(MessageCitation.citation_no))).scalars().all()
    data = [{"citation_no": item.citation_no, "document_id": item.document_id, "document_title": item.document_title, "source_location": item.source_location, "page_number": item.page_number, "quote_excerpt": item.quote_excerpt, "relevance_score": item.relevance_score} for item in citations]
    return SuccessResponse(data=message_data(entity, data), request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.post("/api/v1/messages/{message_id}/feedback", operation_id="createMessageFeedback", status_code=201)
async def create_feedback(message_id: UUID, body: FeedbackRequest, request: Request, user: Annotated[AuthenticatedUser, Depends(require_permissions("chat:use"))], state=Depends(dependency)):
    message = await owned_message(state[0], message_id, user.user_id)
    if message.role != "assistant" or message.status not in {"completed", "fallback"}:
        from app.core.errors import AppError
        raise AppError(status_code=409, code="FEEDBACK_NOT_ALLOWED", message="当前消息不能反馈")
    existing = (await state[0].execute(select(MessageFeedback).where(MessageFeedback.message_id == message_id, MessageFeedback.user_id == user.user_id).with_for_update())).scalar_one_or_none()
    if existing is None:
        from uuid import uuid4
        existing = MessageFeedback(id=uuid4(), message_id=message_id, user_id=user.user_id, rating=body.rating, correction=body.correction)
        state[0].add(existing)
    else:
        existing.rating, existing.correction = body.rating, body.correction
    await state[0].flush()
    return SuccessResponse(data={"id": existing.id, "message_id": existing.message_id, "rating": existing.rating, "correction": existing.correction, "created_at": existing.created_at}, request_id=request.state.request_id, timestamp=datetime.now(UTC))
