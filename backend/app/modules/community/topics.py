from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import (
    CommunityResourceVersionConflict,
    TopicCodeConflict,
    TopicHasPosts,
    TopicNameConflict,
    TopicNotFound,
)
from app.modules.community.models import Topic
from app.modules.community.repositories import TopicRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService


@dataclass(frozen=True)
class TopicData:
    id: UUID
    code: str
    name: str
    description: str | None
    allow_anonymous: bool
    sort_order: int
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TopicPageData:
    items: tuple[TopicData, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class TopicMutationResult:
    status_code: int
    request_id: str
    body: dict[str, object] = field(repr=False)


def topic_data(topic: Topic) -> TopicData:
    return TopicData(
        id=topic.id, code=topic.code, name=topic.name,
        description=topic.description, allow_anonymous=topic.allow_anonymous,
        sort_order=topic.sort_order, status=topic.status, version=topic.version,
        created_at=topic.created_at, updated_at=topic.updated_at,
    )


def topic_payload(topic: Topic) -> dict[str, object]:
    value = topic_data(topic)
    return {
        "id": str(value.id), "code": value.code, "name": value.name,
        "description": value.description, "allow_anonymous": value.allow_anonymous,
        "sort_order": value.sort_order, "status": value.status, "version": value.version,
        "created_at": value.created_at.isoformat(), "updated_at": value.updated_at.isoformat(),
    }


class TopicService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: TopicRepository,
        idempotency: IdempotencyService,
        audit: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._idempotency = idempotency
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))

    async def list(self, *, page: int, page_size: int, status: str = "active") -> TopicPageData:
        result = await self._repository.list(page=page, page_size=page_size, status=status)
        return TopicPageData(tuple(topic_data(item) for item in result.items), page, page_size, result.total)

    async def get(self, topic_id: UUID) -> TopicData:
        item = await self._repository.get(topic_id)
        if item is None:
            raise TopicNotFound()
        return topic_data(item)

    async def create(
        self, *, actor: AuthenticatedUser, code: str, name: str,
        description: str | None, allow_anonymous: bool, sort_order: int,
        idempotency_key: str, request_id: str, request_body: object,
    ) -> TopicMutationResult:
        async with self._session.begin():
            decision = await self._idempotency.begin(
                user_id=actor.user_id, endpoint="POST /api/v1/topics",
                idempotency_key=idempotency_key, request_body=request_body,
            )
            if decision.replay is not None:
                return TopicMutationResult(
                    decision.replay.response_status,
                    str(decision.replay.response_body["request_id"]),
                    dict(decision.replay.response_body),
                )
            if decision.pending:
                raise IdempotencyConflict()
            await self._ensure_unique(code=code, name=name)
            now = self._time()
            item = Topic(
                id=uuid4(), code=code, name=name, description=description,
                allow_anonymous=allow_anonymous, sort_order=sort_order, status="active",
                created_by=actor.user_id, version=1, created_at=now, updated_at=now,
                deleted_at=None,
            )
            self._repository.add(item)
            await self._session.flush()
            body = self._body(item, request_id, now)
            self._audit.record_success(
                action="community.topic.create", resource_type="topic",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(item.id), "code": code, "status": "active"},
            )
            if not await self._idempotency.complete(
                record_id=decision.record_id, response_status=201, response_body=body,
                resource_type="topic", resource_id=str(item.id),
            ):
                raise IdempotencyConflict()
            return TopicMutationResult(201, request_id, body)

    async def update(
        self, *, actor: AuthenticatedUser, topic_id: UUID, version: int,
        changes: dict[str, object], request_id: str,
    ) -> TopicData:
        async with self._session.begin():
            item = await self._repository.get_for_update(topic_id)
            if item is None:
                raise TopicNotFound()
            if item.version != version:
                raise CommunityResourceVersionConflict()
            name = str(changes.get("name", item.name))
            await self._ensure_unique(code=item.code, name=name, excluding=item.id)
            before = {"status": item.status, "version": item.version}
            for key in ("name", "description", "allow_anonymous", "sort_order", "status"):
                if key in changes:
                    setattr(item, key, changes[key])
            item.version += 1
            item.updated_at = self._time()
            await self._session.flush()
            self._audit.record_success(
                action="community.topic.update", resource_type="topic",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data=before, after_data={"status": item.status, "version": item.version},
            )
            return topic_data(item)

    async def delete(self, *, actor: AuthenticatedUser, topic_id: UUID, request_id: str) -> None:
        async with self._session.begin():
            item = await self._repository.get_for_update(topic_id)
            if item is None:
                raise TopicNotFound()
            if await self._repository.has_non_deleted_posts(item.id):
                raise TopicHasPosts()
            item.deleted_at = self._time()
            item.updated_at = item.deleted_at
            item.version += 1
            await self._session.flush()
            self._audit.record_success(
                action="community.topic.delete", resource_type="topic",
                resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data={"status": item.status}, after_data={"deleted": True},
            )

    async def _ensure_unique(self, *, code: str, name: str, excluding: UUID | None = None) -> None:
        if await self._repository.code_exists(code, excluding=excluding):
            raise TopicCodeConflict()
        if await self._repository.name_exists(name, excluding=excluding):
            raise TopicNameConflict()

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _body(item: Topic, request_id: str, now: datetime) -> dict[str, object]:
        return {
            "code": "OK", "message": "success", "data": topic_payload(item),
            "request_id": request_id, "timestamp": now.isoformat(),
        }
