from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import (
    CommentNotFound,
    CommentParentInvalid,
    CommunityAnonymousNotAllowed,
    CommunityResourceVersionConflict,
    PostNotFound,
)
from app.modules.community.models import Comment
from app.modules.community.posts import PublicAuthorData
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import CommentRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser, PermissionDenied
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.moderation import ModerationService


@dataclass(frozen=True)
class CommentData:
    id: UUID
    post_id: UUID
    parent_comment_id: UUID | None
    author: PublicAuthorData
    content_markdown: str
    is_anonymous: bool
    status: str
    moderation_case_id: UUID | None
    published_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CommentPageData:
    items: tuple[CommentData, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class CommentMutationResult:
    status_code: int
    request_id: str
    body: dict[str, object] = field(repr=False)


class CommentService:
    def __init__(
        self, *, session: AsyncSession, repository: CommentRepository,
        profiles: PublicUserProfilePort, moderation: ModerationService,
        idempotency: IdempotencyService, audit: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._profiles = profiles
        self._moderation = moderation
        self._idempotency = idempotency
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))

    async def list(self, *, post_id: UUID, page: int, page_size: int) -> CommentPageData:
        if await self._repository.get_published_post(post_id) is None:
            raise PostNotFound()
        result = await self._repository.list(post_id=post_id, page=page, page_size=page_size)
        return CommentPageData(await self._hydrate_many(result.items, None), page, page_size, result.total)

    async def create(
        self, *, actor: AuthenticatedUser, post_id: UUID,
        parent_comment_id: UUID | None, content_markdown: str, is_anonymous: bool,
        idempotency_key: str, request_id: str, request_body: object,
    ) -> CommentMutationResult:
        async with self._session.begin():
            decision = await self._idempotency.begin(
                user_id=actor.user_id, endpoint=f"POST /api/v1/posts/{post_id}/comments",
                idempotency_key=idempotency_key, request_body=request_body,
            )
            if decision.replay is not None:
                return CommentMutationResult(
                    decision.replay.response_status,
                    str(decision.replay.response_body["request_id"]),
                    dict(decision.replay.response_body),
                )
            if decision.pending:
                raise IdempotencyConflict()
            post = await self._repository.get_published_post(post_id, for_update=True)
            if post is None:
                raise PostNotFound()
            topic = await self._repository.get_topic(post.topic_id)
            if topic is None:
                raise PostNotFound()
            if is_anonymous and not topic.allow_anonymous:
                raise CommunityAnonymousNotAllowed()
            if parent_comment_id is not None and await self._repository.get_published_parent(
                comment_id=parent_comment_id, post_id=post_id,
            ) is None:
                raise CommentParentInvalid()
            now = self._time()
            item = Comment(
                id=uuid4(), post_id=post_id, parent_comment_id=parent_comment_id,
                author_user_id=actor.user_id, content_markdown=content_markdown,
                is_anonymous=is_anonymous, status="pending_review", risk_level="low",
                moderation_case_id=None, moderation_policy_version="m4-sensitive-v1",
                published_at=None, version=1, created_at=now, updated_at=now,
                deleted_at=None,
            )
            await self._scan(item, actor=actor, request_id=request_id, now=now)
            self._repository.add(item)
            if item.status == "published":
                await self._repository.adjust_post_comment_count(post_id, 1)
            await self._session.flush()
            data = await self._hydrate(item, actor)
            body = comment_response_body(data, request_id=request_id, timestamp=now)
            self._audit.record_success(
                action="community.comment.create", resource_type="comment",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(item.id), "post_id": str(post_id),
                            "status": item.status, "version": 1},
            )
            if not await self._idempotency.complete(
                record_id=decision.record_id, response_status=201, response_body=body,
                resource_type="comment", resource_id=str(item.id),
            ):
                raise IdempotencyConflict()
            return CommentMutationResult(201, request_id, body)

    async def update(
        self, *, actor: AuthenticatedUser, comment_id: UUID,
        content_markdown: str, version: int, request_id: str,
    ) -> CommentData:
        async with self._session.begin():
            item = await self._repository.get_for_update(comment_id)
            if item is None:
                raise CommentNotFound()
            self._authorize(item, actor)
            if item.version != version:
                raise CommunityResourceVersionConflict()
            if await self._repository.get_published_post(item.post_id, for_update=True) is None:
                raise PostNotFound()
            was_published = item.status == "published"
            item.content_markdown = content_markdown
            now = self._time()
            await self._scan(item, actor=actor, request_id=request_id, now=now)
            is_published = item.status == "published"
            if was_published != is_published:
                await self._repository.adjust_post_comment_count(item.post_id, 1 if is_published else -1)
            item.version += 1
            item.updated_at = now
            await self._session.flush()
            self._audit.record_success(
                action="community.comment.update", resource_type="comment",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data={"published": was_published, "version": version},
                after_data={"status": item.status, "version": item.version},
            )
            return await self._hydrate(item, actor)

    async def delete(self, *, actor: AuthenticatedUser, comment_id: UUID, request_id: str) -> None:
        async with self._session.begin():
            item = await self._repository.get_for_update(comment_id)
            if item is None:
                raise CommentNotFound()
            self._authorize(item, actor)
            was_published = item.status == "published"
            if was_published:
                await self._repository.adjust_post_comment_count(item.post_id, -1)
            now = self._time()
            item.status = "deleted"; item.deleted_at = now; item.updated_at = now; item.version += 1
            await self._session.flush()
            self._audit.record_success(
                action="community.comment.delete", resource_type="comment",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data={"published": was_published},
                after_data={"deleted": True, "version": item.version},
            )

    async def _scan(
        self, item: Comment, *, actor: AuthenticatedUser, request_id: str, now: datetime,
    ) -> None:
        result = await self._moderation.scan(scope="community", text=item.content_markdown)
        item.content_markdown = result.sanitized_text
        item.status = {"allow": "published", "mask": "published",
                       "review": "pending_review", "block": "rejected"}[result.action]
        item.risk_level = result.risk_level
        item.moderation_policy_version = result.policy_version
        item.published_at = now if item.status == "published" else None
        case = await self._moderation.submit_case(
            result=result, target_module="community", target_type="comment",
            target_id=item.id, content=item.content_markdown,
            submitted_by=actor.user_id, actor=actor, request_id=request_id,
        )
        item.moderation_case_id = case.id if case else None

    async def _hydrate_many(
        self, items: tuple[Comment, ...], actor: AuthenticatedUser | None,
    ) -> tuple[CommentData, ...]:
        ids = {item.author_user_id for item in items if not item.is_anonymous}
        profiles = await self._profiles.get_many(ids) if ids else {}
        return tuple(self._data(item, profiles.get(item.author_user_id), actor) for item in items)

    async def _hydrate(self, item: Comment, actor: AuthenticatedUser) -> CommentData:
        return (await self._hydrate_many((item,), actor))[0]

    @staticmethod
    def _data(item: Comment, profile, actor: AuthenticatedUser | None) -> CommentData:
        author = PublicAuthorData(None, "匿名同学", None, True) if item.is_anonymous else PublicAuthorData(
            item.author_user_id, profile.display_name if profile else "已注销用户",
            profile.avatar_url if profile else None, False,
        )
        privileged = actor is not None and (
            item.author_user_id == actor.user_id or "community:moderate" in actor.permissions
        )
        return CommentData(
            item.id, item.post_id, item.parent_comment_id, author,
            item.content_markdown, item.is_anonymous, item.status,
            item.moderation_case_id if privileged else None, item.published_at,
            item.version, item.created_at, item.updated_at,
        )

    @staticmethod
    def _authorize(item: Comment, actor: AuthenticatedUser) -> None:
        if item.author_user_id != actor.user_id and "community:moderate" not in actor.permissions:
            raise PermissionDenied()

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)


def comment_payload(item: CommentData) -> dict[str, object]:
    return {
        "id": str(item.id), "post_id": str(item.post_id),
        "parent_comment_id": str(item.parent_comment_id) if item.parent_comment_id else None,
        "author": {"user_id": str(item.author.user_id) if item.author.user_id else None,
                   "display_name": item.author.display_name, "avatar_url": item.author.avatar_url,
                   "is_anonymous": item.author.is_anonymous},
        "content_markdown": item.content_markdown, "is_anonymous": item.is_anonymous,
        "status": item.status,
        "moderation_case_id": str(item.moderation_case_id) if item.moderation_case_id else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "version": item.version, "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def comment_response_body(item: CommentData, *, request_id: str, timestamp: datetime) -> dict[str, object]:
    return {"code": "OK", "message": "success", "data": comment_payload(item),
            "request_id": request_id, "timestamp": timestamp.isoformat()}
