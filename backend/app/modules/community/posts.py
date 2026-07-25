from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import (
    CommunityAnonymousNotAllowed,
    CommunityResourceVersionConflict,
    PostNotFound,
    TopicNotFound,
)
from app.modules.community.models import Post, Topic
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import PostRepository
from app.modules.community.topics import TopicData, topic_data
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth import PermissionDenied
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import ScanResult


@dataclass(frozen=True)
class PublicAuthorData:
    user_id: UUID | None
    display_name: str
    avatar_url: str | None
    is_anonymous: bool


@dataclass(frozen=True)
class PostInteractionData:
    liked: bool
    favorited: bool


@dataclass(frozen=True)
class PostData:
    id: UUID
    topic: TopicData
    author: PublicAuthorData
    title: str
    content_markdown: str
    is_anonymous: bool
    status: str
    moderation_case_id: UUID | None
    like_count: int
    favorite_count: int
    comment_count: int
    report_count: int
    interaction: PostInteractionData
    published_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PostPageData:
    items: tuple[PostData, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class PostMutationResult:
    status_code: int
    request_id: str
    body: dict[str, object] = field(repr=False)


class PostQueryService:
    def __init__(self, repository: PostRepository, profiles: PublicUserProfilePort) -> None:
        self._repository = repository
        self._profiles = profiles

    async def list(
        self, *, actor: AuthenticatedUser, page: int, page_size: int,
        topic_id: UUID | None = None, q: str | None = None, mine: bool = False,
        sort: str = "-published_at",
    ) -> PostPageData:
        result = await self._repository.list(
            user_id=actor.user_id, mine=mine, topic_id=topic_id, q=q,
            sort=sort, page=page, page_size=page_size,
        )
        items = await self._hydrate(actor, result.items)
        return PostPageData(items, page, page_size, result.total)

    async def get(self, *, actor: AuthenticatedUser, post_id: UUID) -> PostData:
        item = await self._repository.get_visible(
            post_id=post_id, user_id=actor.user_id,
            moderator="community:moderate" in actor.permissions,
        )
        if item is None:
            raise PostNotFound()
        return (await self._hydrate(actor, (item,)))[0]

    async def hydrate(self, *, actor: AuthenticatedUser, item: Post) -> PostData:
        return (await self._hydrate(actor, (item,)))[0]

    async def _hydrate(
        self, actor: AuthenticatedUser, posts: tuple[Post, ...],
    ) -> tuple[PostData, ...]:
        if not posts:
            return ()
        topic_map = await self._repository.topics_by_ids({item.topic_id for item in posts})
        if len(topic_map) != len({item.topic_id for item in posts}):
            raise PostNotFound()
        interactions = await self._repository.interaction_states(
            post_ids={item.id for item in posts}, user_id=actor.user_id,
        )
        author_ids = {item.author_user_id for item in posts if not item.is_anonymous}
        profiles = await self._profiles.get_many(author_ids) if author_ids else {}
        return tuple(
            self._post_data(item, topic_map[item.topic_id], interactions.get(item.id, set()),
                            profiles.get(item.author_user_id), actor)
            for item in posts
        )

    @staticmethod
    def _post_data(item: Post, topic: Topic, reactions: set[str], profile, actor) -> PostData:
        if item.is_anonymous:
            author = PublicAuthorData(None, "匿名同学", None, True)
        else:
            author = PublicAuthorData(
                item.author_user_id,
                profile.display_name if profile is not None else "已注销用户",
                profile.avatar_url if profile is not None else None,
                False,
            )
        privileged = item.author_user_id == actor.user_id or "community:moderate" in actor.permissions
        return PostData(
            id=item.id, topic=topic_data(topic), author=author, title=item.title,
            content_markdown=item.content_markdown, is_anonymous=item.is_anonymous,
            status=item.status,
            moderation_case_id=item.moderation_case_id if privileged else None,
            like_count=item.like_count, favorite_count=item.favorite_count,
            comment_count=item.comment_count, report_count=item.report_count,
            interaction=PostInteractionData("like" in reactions, "favorite" in reactions),
            published_at=item.published_at, version=item.version,
            created_at=item.created_at, updated_at=item.updated_at,
        )


_ACTION_PRIORITY = {"allow": 0, "mask": 1, "review": 2, "block": 3}
_RISK_PRIORITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class PostService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: PostRepository,
        queries: PostQueryService,
        moderation: ModerationService,
        idempotency: IdempotencyService,
        audit: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._queries = queries
        self._moderation = moderation
        self._idempotency = idempotency
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))

    async def create(
        self,
        *,
        actor: AuthenticatedUser,
        topic_id: UUID,
        title: str,
        content_markdown: str,
        is_anonymous: bool,
        idempotency_key: str,
        request_id: str,
        request_body: object,
        manage_transaction: bool = True,
    ) -> PostMutationResult:
        async with _transaction(self._session, manage_transaction):
            decision = await self._idempotency.begin(
                user_id=actor.user_id,
                endpoint="POST /api/v1/posts",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                return PostMutationResult(
                    decision.replay.response_status,
                    str(decision.replay.response_body["request_id"]),
                    dict(decision.replay.response_body),
                )
            if decision.pending:
                raise IdempotencyConflict()
            topic = await self._require_topic(topic_id, is_anonymous)
            now = self._time()
            item = Post(
                id=uuid4(), topic_id=topic.id, author_user_id=actor.user_id,
                title=title, content_markdown=content_markdown,
                is_anonymous=is_anonymous, status="pending_review", risk_level="low",
                moderation_case_id=None, moderation_policy_version="m4-sensitive-v1",
                like_count=0, favorite_count=0, comment_count=0, report_count=0,
                published_at=None, version=1, created_at=now, updated_at=now,
                deleted_at=None,
            )
            await self._scan_and_apply(item, actor=actor, request_id=request_id, now=now)
            self._repository.add(item)
            await self._session.flush()
            data = await self._queries.hydrate(actor=actor, item=item)
            body = post_response_body(data, request_id=request_id, timestamp=now)
            self._audit.record_success(
                action="community.post.create", resource_type="post",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(item.id), "status": item.status,
                            "risk_level": item.risk_level, "anonymous": item.is_anonymous},
            )
            if not await self._idempotency.complete(
                record_id=decision.record_id, response_status=201, response_body=body,
                resource_type="post", resource_id=str(item.id),
            ):
                raise IdempotencyConflict()
            return PostMutationResult(201, request_id, body)

    async def update(
        self,
        *,
        actor: AuthenticatedUser,
        post_id: UUID,
        version: int,
        changes: dict[str, object],
        request_id: str,
    ) -> PostData:
        async with self._session.begin():
            item = await self._repository.get_for_update(post_id)
            if item is None:
                raise PostNotFound()
            self._authorize_mutation(item, actor)
            if item.version != version:
                raise CommunityResourceVersionConflict()
            topic_id = changes.get("topic_id", item.topic_id)
            anonymous = bool(changes.get("is_anonymous", item.is_anonymous))
            topic = await self._require_topic(topic_id, anonymous)  # type: ignore[arg-type]
            before = {"status": item.status, "version": item.version}
            item.topic_id = topic.id
            if "title" in changes:
                item.title = str(changes["title"])
            if "content_markdown" in changes:
                item.content_markdown = str(changes["content_markdown"])
            item.is_anonymous = anonymous
            now = self._time()
            await self._scan_and_apply(item, actor=actor, request_id=request_id, now=now)
            item.version += 1
            item.updated_at = now
            await self._session.flush()
            self._audit.record_success(
                action="community.post.update", resource_type="post",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data=before,
                after_data={"status": item.status, "version": item.version,
                            "risk_level": item.risk_level, "anonymous": item.is_anonymous},
            )
            return await self._queries.hydrate(actor=actor, item=item)

    async def delete(
        self, *, actor: AuthenticatedUser, post_id: UUID, request_id: str,
    ) -> None:
        async with self._session.begin():
            item = await self._repository.get_for_update(post_id)
            if item is None:
                raise PostNotFound()
            self._authorize_mutation(item, actor)
            now = self._time()
            before = {"status": item.status, "version": item.version}
            item.status = "deleted"
            item.deleted_at = now
            item.updated_at = now
            item.version += 1
            await self._session.flush()
            self._audit.record_success(
                action="community.post.delete", resource_type="post",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data=before, after_data={"deleted": True, "version": item.version},
            )

    async def _require_topic(self, topic_id: UUID, anonymous: bool) -> Topic:
        topic = await self._repository.get_active_topic(topic_id)
        if topic is None:
            raise TopicNotFound()
        if anonymous and not topic.allow_anonymous:
            raise CommunityAnonymousNotAllowed()
        return topic

    @staticmethod
    def _authorize_mutation(item: Post, actor: AuthenticatedUser) -> None:
        if item.author_user_id != actor.user_id and "community:moderate" not in actor.permissions:
            raise PermissionDenied()

    async def _scan_and_apply(
        self, item: Post, *, actor: AuthenticatedUser, request_id: str, now: datetime,
    ) -> None:
        title_result = await self._moderation.scan(scope="community", text=item.title)
        content_result = await self._moderation.scan(
            scope="community", text=item.content_markdown,
        )
        combined = combine_scan_results(title_result, content_result)
        item.title = title_result.sanitized_text
        item.content_markdown = content_result.sanitized_text
        item.status = {
            "allow": "published", "mask": "published",
            "review": "pending_review", "block": "rejected",
        }[combined.action]
        item.risk_level = combined.risk_level
        item.moderation_policy_version = combined.policy_version
        item.published_at = now if item.status == "published" else None
        moderation_case = await self._moderation.submit_case(
            result=combined, target_module="community", target_type="post",
            target_id=item.id, content=f"{item.title}\n{item.content_markdown}",
            submitted_by=actor.user_id, actor=actor, request_id=request_id,
        )
        item.moderation_case_id = moderation_case.id if moderation_case is not None else None

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def combine_scan_results(*results: ScanResult) -> ScanResult:
    action = max(results, key=lambda item: _ACTION_PRIORITY[item.action]).action
    risk = max(results, key=lambda item: _RISK_PRIORITY[item.risk_level]).risk_level
    policy_versions = sorted({item.policy_version for item in results})
    return ScanResult(
        action=action,
        risk_level=risk,
        hits=tuple(hit for item in results for hit in item.hits),
        policy_version="+".join(policy_versions),
        sanitized_text="",
    )


def post_payload(item: PostData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "topic": {
            "id": str(item.topic.id), "code": item.topic.code, "name": item.topic.name,
            "description": item.topic.description,
            "allow_anonymous": item.topic.allow_anonymous,
            "sort_order": item.topic.sort_order, "status": item.topic.status,
            "version": item.topic.version,
            "created_at": item.topic.created_at.isoformat(),
            "updated_at": item.topic.updated_at.isoformat(),
        },
        "author": {
            "user_id": str(item.author.user_id) if item.author.user_id else None,
            "display_name": item.author.display_name, "avatar_url": item.author.avatar_url,
            "is_anonymous": item.author.is_anonymous,
        },
        "title": item.title, "content_markdown": item.content_markdown,
        "is_anonymous": item.is_anonymous, "status": item.status,
        "moderation_case_id": str(item.moderation_case_id) if item.moderation_case_id else None,
        "like_count": item.like_count, "favorite_count": item.favorite_count,
        "comment_count": item.comment_count, "report_count": item.report_count,
        "interaction": {"liked": item.interaction.liked, "favorited": item.interaction.favorited},
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "version": item.version, "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def post_response_body(
    item: PostData, *, request_id: str, timestamp: datetime,
) -> dict[str, object]:
    return {
        "code": "OK", "message": "success", "data": post_payload(item),
        "request_id": request_id, "timestamp": timestamp.isoformat(),
    }


@asynccontextmanager
async def _transaction(session: AsyncSession, manage: bool):
    if manage:
        async with session.begin():
            yield
    else:
        yield
