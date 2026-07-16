from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.models import Post, PostReaction, Topic


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


@dataclass(frozen=True)
class PostPage:
    items: tuple[Post, ...]
    total: int


class PostRepository:
    """Post reads with visibility enforced in SQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, *, user_id: UUID, mine: bool, topic_id: UUID | None,
        q: str | None, sort: str, page: int, page_size: int,
    ) -> PostPage:
        predicates = [Post.deleted_at.is_(None)]
        if mine:
            predicates.append(Post.author_user_id == user_id)
        else:
            predicates.append(Post.status == "published")
        if topic_id is not None:
            predicates.append(Post.topic_id == topic_id)
        normalized = q.strip() if q else ""
        if normalized:
            escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            predicates.append(or_(
                Post.title.ilike(pattern, escape="\\"),
                Post.content_markdown.ilike(pattern, escape="\\"),
            ))
        ordering = func.coalesce(Post.published_at, Post.created_at)
        order = ordering.desc() if sort == "-published_at" else ordering.asc()
        statement = (
            select(Post).where(*predicates).order_by(order, Post.id)
            .offset((page - 1) * page_size).limit(page_size)
        )
        count_statement = select(func.count()).select_from(Post).where(*predicates)
        items = tuple((await self._session.execute(statement)).scalars().all())
        total = int((await self._session.execute(count_statement)).scalar_one())
        return PostPage(items, total)

    async def get_visible(
        self, *, post_id: UUID, user_id: UUID, moderator: bool,
    ) -> Post | None:
        visibility = or_(Post.status == "published", Post.author_user_id == user_id)
        if moderator:
            visibility = or_(visibility, Post.status != "deleted")
        statement = select(Post).where(
            Post.id == post_id, Post.deleted_at.is_(None), visibility,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_for_update(self, post_id: UUID) -> Post | None:
        statement = select(Post).where(
            Post.id == post_id, Post.deleted_at.is_(None),
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_active_topic(self, topic_id: UUID) -> Topic | None:
        statement = select(Topic).where(
            Topic.id == topic_id,
            Topic.status == "active",
            Topic.deleted_at.is_(None),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def topics_by_ids(self, ids: set[UUID]) -> dict[UUID, Topic]:
        if not ids:
            return {}
        statement = select(Topic).where(Topic.id.in_(ids), Topic.deleted_at.is_(None))
        items = (await self._session.execute(statement)).scalars().all()
        return {item.id: item for item in items}

    async def interaction_states(
        self, *, post_ids: set[UUID], user_id: UUID,
    ) -> dict[UUID, set[str]]:
        if not post_ids:
            return {}
        statement = select(PostReaction.post_id, PostReaction.reaction_type).where(
            PostReaction.post_id.in_(post_ids), PostReaction.user_id == user_id,
        )
        result: dict[UUID, set[str]] = {}
        for post_id, reaction_type in (await self._session.execute(statement)).all():
            result.setdefault(post_id, set()).add(reaction_type)
        return result

    def add(self, post: Post) -> None:
        self._session.add(post)
