from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text as sql_text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.models import (
    CampusEvent, Comment, ContentReport, EventRegistration, LostFoundClaim, LostFoundItem,
    LostFoundMatch, Post,
    PostReaction, Topic,
)
from app.modules.platform.models import AppConfig, ModerationCase


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


@dataclass(frozen=True)
class EventPage:
    items: tuple[CampusEvent, ...]
    total: int


class EventRepository:
    """Campus-event reads; the caller owns the session lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, *, user_id: UUID, mine: bool, category: str | None,
        starts_from: object | None, starts_to: object | None, available_only: bool,
        page: int, page_size: int, now: object, q: str | None = None,
    ) -> EventPage:
        predicates = [CampusEvent.deleted_at.is_(None)]
        if mine:
            predicates.append(CampusEvent.organizer_user_id == user_id)
        else:
            predicates.extend((CampusEvent.status == "published", CampusEvent.ends_at > now))
        if category:
            predicates.append(CampusEvent.category == category)
        if starts_from is not None:
            predicates.append(CampusEvent.starts_at >= starts_from)
        if starts_to is not None:
            predicates.append(CampusEvent.starts_at <= starts_to)
        if available_only:
            predicates.extend((
                CampusEvent.status == "published",
                CampusEvent.registration_deadline >= now,
                CampusEvent.starts_at > now,
                CampusEvent.registered_count < CampusEvent.capacity,
            ))
        normalized = q.strip() if q else ""
        if normalized:
            escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            predicates.append(or_(
                CampusEvent.title.ilike(pattern, escape="\\"),
                CampusEvent.description_markdown.ilike(pattern, escape="\\"),
            ))
        statement = (
            select(CampusEvent).where(*predicates)
            .order_by(CampusEvent.starts_at, CampusEvent.id)
            .offset((page - 1) * page_size).limit(page_size)
        )
        count = select(func.count()).select_from(CampusEvent).where(*predicates)
        items = tuple((await self._session.execute(statement)).scalars().all())
        total = int((await self._session.execute(count)).scalar_one())
        return EventPage(items, total)

    async def get_visible(
        self, *, event_id: UUID, user_id: UUID, moderator: bool, now: object,
    ) -> CampusEvent | None:
        visibility = or_(
            (CampusEvent.status == "published") & (CampusEvent.ends_at > now),
            CampusEvent.organizer_user_id == user_id,
        )
        if moderator:
            visibility = or_(visibility, CampusEvent.status != "deleted")
        statement = select(CampusEvent).where(
            CampusEvent.id == event_id, CampusEvent.deleted_at.is_(None), visibility,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def registration_states(
        self, *, event_ids: set[UUID], user_id: UUID,
    ) -> dict[UUID, str]:
        if not event_ids:
            return {}
        statement = select(EventRegistration.event_id, EventRegistration.status).where(
            EventRegistration.event_id.in_(event_ids), EventRegistration.user_id == user_id,
        )
        return dict((await self._session.execute(statement)).all())

    async def get_for_update(self, event_id: UUID) -> CampusEvent | None:
        statement = select(CampusEvent).where(
            CampusEvent.id == event_id, CampusEvent.deleted_at.is_(None),
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add(self, event: CampusEvent) -> None:
        self._session.add(event)

    async def set_registration_lock_timeout(self) -> None:
        await self._session.execute(sql_text("SET LOCAL lock_timeout = '1s'"))

    async def get_registration_for_update(
        self, *, event_id: UUID, user_id: UUID,
    ) -> EventRegistration | None:
        statement = select(EventRegistration).where(
            EventRegistration.event_id == event_id,
            EventRegistration.user_id == user_id,
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_registrations(
        self, *, event_id: UUID, page: int, page_size: int,
    ) -> tuple[tuple[EventRegistration, ...], int]:
        predicate = (EventRegistration.event_id == event_id,)
        statement = select(EventRegistration).where(*predicate).order_by(
            EventRegistration.registered_at, EventRegistration.user_id,
        ).offset((page - 1) * page_size).limit(page_size)
        count = select(func.count()).select_from(EventRegistration).where(*predicate)
        items = tuple((await self._session.execute(statement)).scalars().all())
        return items, int((await self._session.execute(count)).scalar_one())

    def add_registration(self, registration: EventRegistration) -> None:
        self._session.add(registration)


@dataclass(frozen=True)
class LostFoundPage:
    items: tuple[LostFoundItem, ...]
    total: int


class LostFoundRepository:
    """Lost-found persistence with visibility enforced in SQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, *, user_id: UUID, mine: bool, item_type: str | None,
        category: str | None, location: str | None, occurred_from: object | None,
        occurred_to: object | None, page: int, page_size: int,
    ) -> LostFoundPage:
        predicates = [LostFoundItem.deleted_at.is_(None)]
        if mine:
            predicates.append(LostFoundItem.owner_user_id == user_id)
        else:
            predicates.append(LostFoundItem.status.in_(("published", "claiming")))
        if item_type:
            predicates.append(LostFoundItem.item_type == item_type)
        if category:
            predicates.append(LostFoundItem.category == category)
        if location:
            escaped = location.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if escaped:
                predicates.append(LostFoundItem.location.ilike(f"%{escaped}%", escape="\\"))
        if occurred_from is not None:
            predicates.append(LostFoundItem.occurred_at >= occurred_from)
        if occurred_to is not None:
            predicates.append(LostFoundItem.occurred_at <= occurred_to)
        statement = select(LostFoundItem).where(*predicates).order_by(
            LostFoundItem.occurred_at.desc(), LostFoundItem.id.desc(),
        ).offset((page - 1) * page_size).limit(page_size)
        count = select(func.count()).select_from(LostFoundItem).where(*predicates)
        items = tuple((await self._session.execute(statement)).scalars().all())
        return LostFoundPage(items, int((await self._session.execute(count)).scalar_one()))

    async def get_visible(
        self, *, item_id: UUID, user_id: UUID, moderator: bool,
    ) -> LostFoundItem | None:
        visibility = or_(
            LostFoundItem.status.in_(("published", "claiming")),
            LostFoundItem.owner_user_id == user_id,
        )
        if moderator:
            visibility = or_(visibility, LostFoundItem.status != "deleted")
        statement = select(LostFoundItem).where(
            LostFoundItem.id == item_id, LostFoundItem.deleted_at.is_(None), visibility,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_for_update(self, item_id: UUID) -> LostFoundItem | None:
        statement = select(LostFoundItem).where(
            LostFoundItem.id == item_id, LostFoundItem.deleted_at.is_(None),
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add(self, item: LostFoundItem) -> None:
        self._session.add(item)

    async def has_active_claim(self, item_id: UUID) -> bool:
        statement = select(LostFoundClaim.id).where(
            LostFoundClaim.target_item_id == item_id,
            LostFoundClaim.status.in_(("pending", "verified")),
        ).limit(1)
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def match_config(self) -> dict[str, object]:
        keys = (
            "community.match.category_weight", "community.match.location_weight",
            "community.match.time_weight", "community.match.keyword_weight",
            "community.match.threshold", "community.match.time_window_days",
        )
        statement = select(AppConfig.key, AppConfig.value).where(AppConfig.key.in_(keys))
        return dict((await self._session.execute(statement)).all())

    async def match_candidates(
        self, source: LostFoundItem, *, window_start: object, limit: int = 200,
    ) -> tuple[LostFoundItem, ...]:
        statement = select(LostFoundItem).where(
            LostFoundItem.id != source.id,
            LostFoundItem.owner_user_id != source.owner_user_id,
            LostFoundItem.item_type != source.item_type,
            LostFoundItem.status.in_(("published", "claiming")),
            LostFoundItem.deleted_at.is_(None),
            LostFoundItem.occurred_at >= window_start,
        ).order_by(LostFoundItem.occurred_at.desc(), LostFoundItem.id).limit(limit)
        return tuple((await self._session.execute(statement)).scalars().all())

    async def upsert_match(
        self, *, source_id: UUID, candidate_id: UUID, score: object,
        reasons: list[object], algorithm_version: str,
    ) -> LostFoundMatch:
        statement = insert(LostFoundMatch).values(source_item_id=source_id,
            candidate_item_id=candidate_id, score=score, reasons=reasons,
            algorithm_version=algorithm_version).on_conflict_do_update(
                index_elements=(LostFoundMatch.source_item_id, LostFoundMatch.candidate_item_id,
                                LostFoundMatch.algorithm_version),
                set_={"score": score, "reasons": reasons},
            ).returning(LostFoundMatch)
        return (await self._session.execute(statement)).scalar_one()

    async def delete_stale_matches(
        self, *, source_id: UUID, algorithm_version: str, keep_ids: set[UUID],
    ) -> None:
        statement = delete(LostFoundMatch).where(
            LostFoundMatch.source_item_id == source_id,
            LostFoundMatch.algorithm_version == algorithm_version,
        )
        if keep_ids:
            statement = statement.where(LostFoundMatch.candidate_item_id.not_in(keep_ids))
        await self._session.execute(statement)

    async def list_matches(
        self, *, source_id: UUID, page: int, page_size: int,
    ) -> tuple[tuple[LostFoundMatch, ...], int]:
        predicate = (LostFoundMatch.source_item_id == source_id,)
        statement = select(LostFoundMatch).where(*predicate).order_by(
            LostFoundMatch.score.desc(), LostFoundMatch.created_at.desc(), LostFoundMatch.id,
        ).offset((page - 1) * page_size).limit(page_size)
        count = select(func.count()).select_from(LostFoundMatch).where(*predicate)
        rows = tuple((await self._session.execute(statement)).scalars().all())
        return rows, int((await self._session.execute(count)).scalar_one())

    async def items_by_ids(self, ids: set[UUID]) -> dict[UUID, LostFoundItem]:
        if not ids:
            return {}
        statement = select(LostFoundItem).where(LostFoundItem.id.in_(ids))
        rows = (await self._session.execute(statement)).scalars().all()
        return {row.id: row for row in rows}


class LostFoundClaimRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_target_for_update(self, item_id: UUID) -> LostFoundItem | None:
        statement = select(LostFoundItem).where(
            LostFoundItem.id == item_id, LostFoundItem.deleted_at.is_(None),
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_claimant_item(self, item_id: UUID) -> LostFoundItem | None:
        statement = select(LostFoundItem).where(
            LostFoundItem.id == item_id, LostFoundItem.deleted_at.is_(None),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def active_exists(self, *, target_id: UUID, claimant_id: UUID) -> bool:
        statement = select(LostFoundClaim.id).where(
            LostFoundClaim.target_item_id == target_id,
            LostFoundClaim.claimant_user_id == claimant_id,
            LostFoundClaim.status.in_(("pending", "verified")),
        ).limit(1)
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def get_visible(self, *, claim_id: UUID, user_id: UUID,
                          for_update: bool = False) -> LostFoundClaim | None:
        owner = select(LostFoundItem.id).where(
            LostFoundItem.id == LostFoundClaim.target_item_id,
            LostFoundItem.owner_user_id == user_id,
        ).exists()
        statement = select(LostFoundClaim).where(
            LostFoundClaim.id == claim_id,
            or_(LostFoundClaim.claimant_user_id == user_id, owner),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_visible(
        self, *, user_id: UUID, role: str, status: str | None,
        page: int, page_size: int,
    ) -> tuple[tuple[LostFoundClaim, ...], int]:
        owner = select(LostFoundItem.id).where(
            LostFoundItem.id == LostFoundClaim.target_item_id,
            LostFoundItem.owner_user_id == user_id,
        ).exists()
        if role == "claimant":
            visibility = LostFoundClaim.claimant_user_id == user_id
        elif role == "owner":
            visibility = owner
        else:
            visibility = or_(LostFoundClaim.claimant_user_id == user_id, owner)
        predicates = [visibility]
        if status:
            predicates.append(LostFoundClaim.status == status)
        statement = select(LostFoundClaim).where(*predicates).order_by(
            LostFoundClaim.created_at.desc(), LostFoundClaim.id,
        ).offset((page - 1) * page_size).limit(page_size)
        count = select(func.count()).select_from(LostFoundClaim).where(*predicates)
        rows = tuple((await self._session.execute(statement)).scalars().all())
        return rows, int((await self._session.execute(count)).scalar_one())

    async def targets_by_ids(self, ids: set[UUID]) -> dict[UUID, LostFoundItem]:
        if not ids:
            return {}
        statement = select(LostFoundItem).where(LostFoundItem.id.in_(ids))
        rows = (await self._session.execute(statement)).scalars().all()
        return {row.id: row for row in rows}

    def add(self, claim: LostFoundClaim) -> None:
        self._session.add(claim)


@dataclass(frozen=True)
class CommentPage:
    items: tuple[Comment, ...]
    total: int


class CommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_published_post(self, post_id: UUID, *, for_update: bool = False) -> Post | None:
        statement = select(Post).where(
            Post.id == post_id, Post.status == "published", Post.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_topic(self, topic_id: UUID) -> Topic | None:
        statement = select(Topic).where(Topic.id == topic_id, Topic.deleted_at.is_(None))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list(self, *, post_id: UUID, page: int, page_size: int) -> CommentPage:
        predicates = (
            Comment.post_id == post_id, Comment.status == "published",
            Comment.deleted_at.is_(None),
        )
        statement = (
            select(Comment).where(*predicates).order_by(Comment.created_at, Comment.id)
            .offset((page - 1) * page_size).limit(page_size)
        )
        count = select(func.count()).select_from(Comment).where(*predicates)
        items = tuple((await self._session.execute(statement)).scalars().all())
        total = int((await self._session.execute(count)).scalar_one())
        return CommentPage(items, total)

    async def get_published_parent(self, *, comment_id: UUID, post_id: UUID) -> Comment | None:
        statement = select(Comment).where(
            Comment.id == comment_id, Comment.post_id == post_id,
            Comment.status == "published", Comment.deleted_at.is_(None),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_for_update(self, comment_id: UUID) -> Comment | None:
        statement = select(Comment).where(
            Comment.id == comment_id, Comment.deleted_at.is_(None),
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def adjust_post_comment_count(self, post_id: UUID, delta: int) -> None:
        value = Post.comment_count + delta if delta > 0 else func.greatest(Post.comment_count + delta, 0)
        await self._session.execute(
            update(Post).where(Post.id == post_id).values(comment_count=value)
        )

    def add(self, comment: Comment) -> None:
        self._session.add(comment)


class ReactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_post_for_update(self, post_id: UUID) -> Post | None:
        statement = select(Post).where(
            Post.id == post_id, Post.deleted_at.is_(None),
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def insert(self, *, post_id: UUID, user_id: UUID, reaction_type: str) -> bool:
        statement = (
            insert(PostReaction).values(
                post_id=post_id, user_id=user_id, reaction_type=reaction_type,
            ).on_conflict_do_nothing().returning(PostReaction.post_id)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def delete(self, *, post_id: UUID, user_id: UUID, reaction_type: str) -> bool:
        statement = delete(PostReaction).where(
            PostReaction.post_id == post_id, PostReaction.user_id == user_id,
            PostReaction.reaction_type == reaction_type,
        ).returning(PostReaction.post_id)
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def adjust_count(self, *, post_id: UUID, reaction_type: str, delta: int) -> tuple[int, int]:
        column = Post.like_count if reaction_type == "like" else Post.favorite_count
        value = column + delta if delta > 0 else func.greatest(column + delta, 0)
        statement = update(Post).where(Post.id == post_id).values({column.key: value}).returning(
            Post.like_count, Post.favorite_count,
        )
        row = (await self._session.execute(statement)).one()
        return int(row[0]), int(row[1])


@dataclass(frozen=True)
class ReportTarget:
    item: Post | Comment | CampusEvent | LostFoundItem
    target_type: str


class ReportRepository:
    _MODELS = {
        "post": (Post, Post.author_user_id, ("published",)),
        "comment": (Comment, Comment.author_user_id, ("published",)),
        "event": (CampusEvent, CampusEvent.organizer_user_id, ("published",)),
        "lost_found": (LostFoundItem, LostFoundItem.owner_user_id, ("published", "claiming")),
    }

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_target_for_update(
        self, *, target_type: str, target_id: UUID, user_id: UUID, moderator: bool,
    ) -> ReportTarget | None:
        model, owner_column, public_statuses = self._MODELS[target_type]
        visibility = model.status.in_(public_statuses)
        if not moderator:
            visibility = or_(visibility, owner_column == user_id)
        statement = select(model).where(
            model.id == target_id, model.deleted_at.is_(None), visibility,
        ).with_for_update()
        item = (await self._session.execute(statement)).scalar_one_or_none()
        return ReportTarget(item, target_type) if item is not None else None

    async def get_existing(
        self, *, reporter_user_id: UUID, target_type: str, target_id: UUID,
    ) -> ContentReport | None:
        statement = select(ContentReport).where(
            ContentReport.reporter_user_id == reporter_user_id,
            ContentReport.target_type == target_type, ContentReport.target_id == target_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_pending_case(self, *, target_type: str, target_id: UUID) -> ModerationCase | None:
        statement = select(ModerationCase).where(
            ModerationCase.target_module == "community",
            ModerationCase.target_type == target_type,
            ModerationCase.target_id == target_id,
            ModerationCase.status == "pending",
        ).order_by(ModerationCase.created_at, ModerationCase.id).limit(1).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def increment_post_report_count(self, post_id: UUID) -> None:
        await self._session.execute(
            update(Post).where(Post.id == post_id).values(report_count=Post.report_count + 1)
        )

    def add(self, report: ContentReport) -> None:
        self._session.add(report)
