from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import User


@dataclass(frozen=True)
class PublicUserProfile:
    user_id: UUID
    display_name: str
    avatar_url: str | None = None


class PublicUserProfilePort(Protocol):
    async def get_many(self, user_ids: set[UUID]) -> dict[UUID, PublicUserProfile]: ...


class PlatformPublicUserProfileAdapter:
    """M4-owned batch adapter exposing only public profile fields."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_many(self, user_ids: set[UUID]) -> dict[UUID, PublicUserProfile]:
        if not user_ids:
            return {}
        statement = select(User.id, User.display_name).where(User.id.in_(user_ids))
        return {
            user_id: PublicUserProfile(user_id, display_name)
            for user_id, display_name in (await self._session.execute(statement)).all()
        }

    async def usernames(self, user_ids: set[UUID]) -> dict[UUID, str]:
        if not user_ids:
            return {}
        statement = select(User.id, User.username).where(User.id.in_(user_ids))
        return dict((await self._session.execute(statement)).all())
