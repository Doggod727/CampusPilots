from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.models import Post, Topic


@dataclass(frozen=True)
class TopicPage:
    items: tuple[Topic, ...]
    total: int


class TopicRepository:
    """Topic persistence using a caller-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, *, page: int, page_size: int, status: str) -> TopicPage:
        predicate = (Topic.deleted_at.is_(None), Topic.status == status)
        statement = (
            select(Topic)
            .where(*predicate)
            .order_by(Topic.sort_order, Topic.name, Topic.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        count_statement = select(func.count()).select_from(Topic).where(*predicate)
        items = tuple((await self._session.execute(statement)).scalars().all())
        total = int((await self._session.execute(count_statement)).scalar_one())
        return TopicPage(items, total)

    async def get(self, topic_id: UUID) -> Topic | None:
        statement = select(Topic).where(Topic.id == topic_id, Topic.deleted_at.is_(None))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_for_update(self, topic_id: UUID) -> Topic | None:
        statement = (
            select(Topic)
            .where(Topic.id == topic_id, Topic.deleted_at.is_(None))
            .with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def code_exists(self, code: str, *, excluding: UUID | None = None) -> bool:
        statement = select(Topic.id).where(Topic.code == code, Topic.deleted_at.is_(None))
        if excluding is not None:
            statement = statement.where(Topic.id != excluding)
        return (await self._session.execute(statement.limit(1))).scalar_one_or_none() is not None

    async def name_exists(self, name: str, *, excluding: UUID | None = None) -> bool:
        statement = select(Topic.id).where(
            func.lower(Topic.name) == name.casefold(), Topic.deleted_at.is_(None)
        )
        if excluding is not None:
            statement = statement.where(Topic.id != excluding)
        return (await self._session.execute(statement.limit(1))).scalar_one_or_none() is not None

    async def has_non_deleted_posts(self, topic_id: UUID) -> bool:
        statement = select(Post.id).where(
            Post.topic_id == topic_id, Post.deleted_at.is_(None)
        ).limit(1)
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    def add(self, topic: Topic) -> None:
        self._session.add(topic)
