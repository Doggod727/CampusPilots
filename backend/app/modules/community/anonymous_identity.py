from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import AnonymousIdentityNotFound
from app.modules.community.models import Comment, Post
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.models import User


@dataclass(frozen=True)
class HistoricalIdentity:
    user_id: UUID
    username: str
    display_name: str


class HistoricalIdentityPort(Protocol):
    async def get(self, user_id: UUID) -> HistoricalIdentity | None: ...


class PlatformHistoricalIdentityAdapter:
    """Governance-only identity adapter; intentionally includes soft-deleted users."""
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> HistoricalIdentity | None:
        row = (await self._session.execute(
            select(User.id, User.username, User.display_name).where(User.id == user_id)
        )).one_or_none()
        return HistoricalIdentity(row[0], str(row[1]), row[2]) if row else None


@dataclass(frozen=True)
class AnonymousIdentityData:
    target_type: str
    target_id: UUID
    author_user_id: UUID
    username: str
    display_name: str
    reason: str
    revealed_at: datetime


class AnonymousIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_author(self, *, target_type: str, target_id: UUID) -> UUID | None:
        model = Post if target_type == "post" else Comment
        statement = select(model.author_user_id).where(
            model.id == target_id, model.is_anonymous.is_(True), model.deleted_at.is_(None),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()


class AnonymousIdentityService:
    def __init__(
        self, *, session: AsyncSession, repository: AnonymousIdentityRepository,
        identities: HistoricalIdentityPort, audit: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session; self._repository = repository
        self._identities = identities; self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))

    async def reveal(
        self, *, actor: AuthenticatedUser, target_type: str, target_id: UUID,
        reason: str, request_id: str,
    ) -> AnonymousIdentityData:
        result: AnonymousIdentityData | None = None
        error: AnonymousIdentityNotFound | None = None
        async with self._session.begin():
            author_id = await self._repository.get_author(
                target_type=target_type, target_id=target_id,
            )
            identity = await self._identities.get(author_id) if author_id else None
            audit_data = {"target_type": target_type, "target_id": str(target_id),
                          "reason": reason}
            if identity is None:
                error = AnonymousIdentityNotFound()
                self._audit.record_failure(
                    action="community.anonymous_identity.read",
                    resource_type=target_type, resource_id=str(target_id),
                    request_id=request_id, error_code=error.code,
                    actor_user_id=actor.user_id, actor_username=actor.username,
                    after_data=audit_data,
                )
            else:
                revealed_at = self._time()
                result = AnonymousIdentityData(
                    target_type, target_id, identity.user_id, identity.username,
                    identity.display_name, reason, revealed_at,
                )
                self._audit.record_success(
                    action="community.anonymous_identity.read",
                    resource_type=target_type, resource_id=str(target_id),
                    request_id=request_id, actor_user_id=actor.user_id,
                    actor_username=actor.username, after_data=audit_data,
                )
            await self._session.flush()
        if error is not None:
            raise error
        assert result is not None
        return result

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)
