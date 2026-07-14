from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.models import SensitiveWord
from app.modules.platform.moderation_scan import InvalidSensitiveWordRule, validate_rule
from app.modules.platform.repositories import AuditLogRepository, SensitiveWordRepository


class DuplicateSensitiveWord(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="DUPLICATE_RESOURCE", message="敏感词规则已存在")


class SensitiveWordNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="SENSITIVE_WORD_NOT_FOUND", message="敏感词规则不存在")


class SensitiveWordService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: SensitiveWordRepository,
        audit_service: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit_service = audit_service
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def create(
        self,
        *,
        actor: AuthenticatedUser,
        word: str,
        match_type: str,
        action: str,
        replacement: str | None,
        scope: str,
        enabled: bool,
        request_id: str,
    ) -> SensitiveWord:
        if match_type == "regex":
            candidate = SensitiveWord(word=word, match_type=match_type, action=action, scope=scope)
            validate_rule(candidate)
        if action == "mask" and replacement is None:
            raise InvalidSensitiveWordRule()
        async with self._session.begin():
            if await self._repository.get_by_rule(word=word, match_type=match_type, scope=scope):
                raise DuplicateSensitiveWord()
            now = self._current_time()
            rule = SensitiveWord(
                id=uuid4(), word=word, match_type=match_type, action=action,
                replacement=replacement, scope=scope, enabled=enabled,
                created_by=actor.user_id, created_at=now, updated_at=now,
            )
            self._repository.add(rule)
            try:
                await self._session.flush()
            except IntegrityError:
                raise DuplicateSensitiveWord() from None
            self._audit_service.record_success(
                action="sensitive_word.create", resource_type="sensitive_word",
                resource_id=str(rule.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={
                    "id": str(rule.id), "match_type": rule.match_type,
                    "action": rule.action, "scope": rule.scope, "enabled": rule.enabled,
                },
            )
            return rule

    async def delete(self, *, actor: AuthenticatedUser, word_id: UUID, request_id: str) -> None:
        async with self._session.begin():
            rule = await self._repository.get_by_id(word_id)
            if rule is None:
                raise SensitiveWordNotFound()
            if not await self._repository.delete(word_id):
                raise SensitiveWordNotFound()
            self._audit_service.record_success(
                action="sensitive_word.delete", resource_type="sensitive_word",
                resource_id=str(word_id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data={
                    "id": str(rule.id), "match_type": rule.match_type,
                    "action": rule.action, "scope": rule.scope,
                }, after_data={"status": "deleted"},
            )

    def _current_time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@asynccontextmanager
async def sensitive_word_service_context(settings: Settings) -> AsyncIterator[SensitiveWordService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield SensitiveWordService(
                session=session,
                repository=SensitiveWordRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
            )
    finally:
        await database.dispose()
