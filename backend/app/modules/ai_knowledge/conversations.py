from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.ai_knowledge.models import Conversation, Message


class ConversationNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="CONVERSATION_NOT_FOUND", message="会话不存在")


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_owned(self, conversation_id: UUID, user_id: UUID, lock: bool = False):
        statement = select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
        if lock:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def next_sequence(self, conversation_id: UUID) -> int:
        value = await self.session.scalar(select(func.coalesce(func.max(Message.sequence_no), 0)).where(Message.conversation_id == conversation_id))
        return int(value) + 1


class ConversationService:
    terminal = {"completed", "failed", "cancelled", "fallback"}

    def __init__(self, session: AsyncSession, repository: ConversationRepository) -> None:
        self.session = session
        self.repository = repository

    async def create(self, user_id: UUID, title: str = "新对话") -> Conversation:
        entity = Conversation(id=uuid4(), user_id=user_id, title=title, status="active")
        self.session.add(entity)
        return entity

    async def append_turn(self, conversation_id: UUID, user_id: UUID, content: str, request_id: str) -> tuple[Message, Message]:
        conversation = await self.repository.get_owned(conversation_id, user_id, lock=True)
        if conversation is None:
            raise ConversationNotFound()
        existing = (await self.session.execute(select(Message).where(Message.conversation_id == conversation_id, Message.request_id == request_id).order_by(Message.sequence_no))).scalars().all()
        if len(existing) >= 2:
            return existing[0], existing[1]
        sequence = await self.repository.next_sequence(conversation_id)
        user_message = Message(id=uuid4(), conversation_id=conversation_id, sequence_no=sequence, role="user", status="completed", content=content, request_id=request_id, completed_at=datetime.now(UTC))
        assistant = Message(id=uuid4(), conversation_id=conversation_id, sequence_no=sequence + 1, role="assistant", status="pending", content="", reply_to_message_id=user_message.id, request_id=request_id)
        self.session.add_all([user_message, assistant])
        conversation.last_message_at = datetime.now(UTC)
        return user_message, assistant

    @staticmethod
    def complete(message: Message, content: str, finish_reason: str = "stop") -> None:
        if message.status in ConversationService.terminal:
            raise AppError(status_code=409, code="MESSAGE_STATE_CONFLICT", message="消息状态冲突")
        message.status, message.content, message.finish_reason = "completed", content, finish_reason
        message.completed_at = datetime.now(UTC)

    @staticmethod
    def fail(message: Message, error_code: str) -> None:
        if message.status in ConversationService.terminal:
            raise AppError(status_code=409, code="MESSAGE_STATE_CONFLICT", message="消息状态冲突")
        message.status, message.error_code, message.content = "failed", error_code, ""
        message.completed_at = datetime.now(UTC)
