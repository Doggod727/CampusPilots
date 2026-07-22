from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import (
    CommunityResourceVersionConflict, EventCapacityInvalid, EventNotFound,
    EventStateInvalid, EventTimeInvalid,
)
from app.modules.community.models import CampusEvent
from app.modules.community.posts import PublicAuthorData, combine_scan_results
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import EventRepository
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth import PermissionDenied
from app.modules.platform.audit import AuditService
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.moderation import ModerationService


@dataclass(frozen=True)
class EventData:
    id: UUID
    organizer: PublicAuthorData
    title: str
    description_markdown: str
    category: str
    location: str
    starts_at: datetime
    ends_at: datetime
    registration_deadline: datetime
    capacity: int
    registered_count: int
    status: str
    my_registration_status: str | None
    cancellation_reason: str | None
    moderation_case_id: UUID | None
    published_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EventPageData:
    items: tuple[EventData, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class EventMutationResult:
    status_code: int
    request_id: str
    body: dict[str, object] = field(repr=False)


class EventQueryService:
    def __init__(
        self, repository: EventRepository, profiles: PublicUserProfilePort,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._profiles = profiles
        self._now = now or (lambda: datetime.now(UTC))

    async def list(
        self, *, actor: AuthenticatedUser, page: int, page_size: int,
        category: str | None = None, starts_from: datetime | None = None,
        starts_to: datetime | None = None, available_only: bool = False,
        mine: bool = False, q: str | None = None,
    ) -> EventPageData:
        result = await self._repository.list(
            user_id=actor.user_id, mine=mine, category=category,
            starts_from=starts_from, starts_to=starts_to, available_only=available_only,
            page=page, page_size=page_size, now=self._time(), q=q,
        )
        items = await self._hydrate(actor, result.items)
        return EventPageData(items, page, page_size, result.total)

    async def get(self, *, actor: AuthenticatedUser, event_id: UUID) -> EventData:
        item = await self._repository.get_visible(
            event_id=event_id, user_id=actor.user_id,
            moderator="community:moderate" in actor.permissions, now=self._time(),
        )
        if item is None:
            raise EventNotFound()
        return (await self._hydrate(actor, (item,)))[0]

    async def _hydrate(
        self, actor: AuthenticatedUser, items: tuple[CampusEvent, ...],
    ) -> tuple[EventData, ...]:
        profiles = await self._profiles.get_many({item.organizer_user_id for item in items})
        states = await self._repository.registration_states(
            event_ids={item.id for item in items}, user_id=actor.user_id,
        )
        privileged = "community:moderate" in actor.permissions
        output = []
        for item in items:
            profile = profiles.get(item.organizer_user_id)
            organizer = PublicAuthorData(
                user_id=item.organizer_user_id,
                display_name=profile.display_name if profile else "未知用户",
                avatar_url=profile.avatar_url if profile else None,
                is_anonymous=False,
            )
            output.append(EventData(
                id=item.id, organizer=organizer, title=item.title,
                description_markdown=item.description_markdown, category=item.category,
                location=item.location, starts_at=item.starts_at, ends_at=item.ends_at,
                registration_deadline=item.registration_deadline, capacity=item.capacity,
                registered_count=item.registered_count, status=item.status,
                my_registration_status=states.get(item.id),
                cancellation_reason=item.cancellation_reason,
                moderation_case_id=item.moderation_case_id if privileged or item.organizer_user_id == actor.user_id else None,
                published_at=item.published_at, version=item.version,
                created_at=item.created_at, updated_at=item.updated_at,
            ))
        return tuple(output)

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class EventService:
    def __init__(
        self, *, session: AsyncSession, repository: EventRepository,
        queries: EventQueryService, moderation: ModerationService,
        idempotency: IdempotencyService, audit: AuditService,
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
        self, *, actor: AuthenticatedUser, idempotency_key: str, request_id: str,
        request_body: object, title: str, description_markdown: str, category: str,
        location: str, starts_at: datetime, ends_at: datetime,
        registration_deadline: datetime, capacity: int,
        manage_transaction: bool = True,
    ) -> EventMutationResult:
        now = self._time()
        self._validate_times(starts_at, ends_at, registration_deadline, now=now)
        async with _transaction(self._session, manage_transaction):
            decision = await self._idempotency.begin(
                user_id=actor.user_id, endpoint="POST /api/v1/events",
                idempotency_key=idempotency_key, request_body=request_body,
            )
            if decision.replay is not None:
                return EventMutationResult(decision.replay.response_status,
                    str(decision.replay.response_body["request_id"]), dict(decision.replay.response_body))
            if decision.pending:
                raise IdempotencyConflict()
            item = CampusEvent(
                id=uuid4(), organizer_user_id=actor.user_id, title=title,
                description_markdown=description_markdown, category=category,
                location=location, starts_at=starts_at, ends_at=ends_at,
                registration_deadline=registration_deadline, capacity=capacity,
                registered_count=0, status="pending_review", risk_level="low",
                moderation_case_id=None, moderation_policy_version="m4-sensitive-v1",
                cancellation_reason=None, published_at=None, version=1,
                created_at=now, updated_at=now, deleted_at=None,
            )
            await self._scan(item, actor=actor, request_id=request_id, now=now)
            self._repository.add(item)
            await self._session.flush()
            data = await self._queries._hydrate(actor, (item,))
            body = event_response_body(data[0], request_id=request_id, timestamp=now)
            self._audit.record_success(
                action="community.event.create", resource_type="event", resource_id=str(item.id),
                request_id=request_id, actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(item.id), "status": item.status, "risk_level": item.risk_level},
            )
            if not await self._idempotency.complete(
                record_id=decision.record_id, response_status=201, response_body=body,
                resource_type="event", resource_id=str(item.id),
            ):
                raise IdempotencyConflict()
            return EventMutationResult(201, request_id, body)

    async def update(
        self, *, actor: AuthenticatedUser, event_id: UUID, version: int,
        changes: dict[str, object], request_id: str,
    ) -> EventData:
        async with self._session.begin():
            item = await self._repository.get_for_update(event_id)
            self._authorize(item, actor)
            assert item is not None
            if item.version != version:
                raise CommunityResourceVersionConflict()
            if item.status in {"ended", "cancelled", "rejected", "deleted"}:
                raise EventStateInvalid()
            starts_at = changes.get("starts_at", item.starts_at)
            ends_at = changes.get("ends_at", item.ends_at)
            deadline = changes.get("registration_deadline", item.registration_deadline)
            self._validate_times(starts_at, ends_at, deadline, now=self._time())  # type: ignore[arg-type]
            capacity = int(changes.get("capacity", item.capacity))
            if capacity < item.registered_count:
                raise EventCapacityInvalid()
            for key in ("title", "description_markdown", "category", "location", "starts_at", "ends_at", "registration_deadline"):
                if key in changes:
                    setattr(item, key, changes[key])
            item.capacity = capacity
            now = self._time()
            await self._scan(item, actor=actor, request_id=request_id, now=now)
            item.version += 1
            item.updated_at = now
            await self._session.flush()
            self._audit.record_success(
                action="community.event.update", resource_type="event", resource_id=str(item.id),
                request_id=request_id, actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(item.id), "status": item.status, "version": item.version},
            )
            return (await self._queries._hydrate(actor, (item,)))[0]

    async def cancel(
        self, *, actor: AuthenticatedUser, event_id: UUID, version: int, reason: str,
        idempotency_key: str, request_id: str, request_body: object,
    ) -> EventMutationResult:
        async with self._session.begin():
            decision = await self._idempotency.begin(
                user_id=actor.user_id, endpoint=f"POST /api/v1/events/{event_id}/cancel",
                idempotency_key=idempotency_key, request_body=request_body,
            )
            if decision.replay is not None:
                return EventMutationResult(decision.replay.response_status,
                    str(decision.replay.response_body["request_id"]), dict(decision.replay.response_body))
            if decision.pending:
                raise IdempotencyConflict()
            item = await self._repository.get_for_update(event_id)
            self._authorize(item, actor)
            assert item is not None
            if item.version != version:
                raise CommunityResourceVersionConflict()
            if item.status not in {"pending_review", "published"}:
                raise EventStateInvalid()
            now = self._time()
            item.status = "cancelled"
            item.cancellation_reason = reason
            item.version += 1
            item.updated_at = now
            await self._session.flush()
            data = (await self._queries._hydrate(actor, (item,)))[0]
            body = event_response_body(data, request_id=request_id, timestamp=now)
            self._audit.record_success(
                action="community.event.cancel", resource_type="event", resource_id=str(item.id),
                request_id=request_id, actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(item.id), "status": "cancelled", "version": item.version},
            )
            if not await self._idempotency.complete(
                record_id=decision.record_id, response_status=200, response_body=body,
                resource_type="event", resource_id=str(item.id),
            ):
                raise IdempotencyConflict()
            return EventMutationResult(200, request_id, body)

    async def _scan(self, item: CampusEvent, *, actor: AuthenticatedUser, request_id: str, now: datetime) -> None:
        title = await self._moderation.scan(scope="community", text=item.title)
        description = await self._moderation.scan(scope="community", text=item.description_markdown)
        combined = combine_scan_results(title, description)
        item.title, item.description_markdown = title.sanitized_text, description.sanitized_text
        item.status = {"allow": "published", "mask": "published", "review": "pending_review", "block": "rejected"}[combined.action]
        item.risk_level = combined.risk_level
        item.moderation_policy_version = combined.policy_version
        item.published_at = now if item.status == "published" else None
        case = await self._moderation.submit_case(
            result=combined, target_module="community", target_type="event", target_id=item.id,
            content=f"{item.title}\n{item.description_markdown}", submitted_by=actor.user_id,
            actor=actor, request_id=request_id,
        )
        item.moderation_case_id = case.id if case else None

    @staticmethod
    def _authorize(item: CampusEvent | None, actor: AuthenticatedUser) -> None:
        if item is None:
            raise EventNotFound()
        if item.organizer_user_id != actor.user_id and "community:moderate" not in actor.permissions:
            raise PermissionDenied()

    @staticmethod
    def _validate_times(starts_at: datetime, ends_at: datetime, deadline: datetime, *, now: datetime) -> None:
        if starts_at <= now or starts_at >= ends_at or deadline > starts_at:
            raise EventTimeInvalid()

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)


def event_payload(item: EventData) -> dict[str, object]:
    return {
        "id": str(item.id), "organizer": {
            "user_id": str(item.organizer.user_id) if item.organizer.user_id else None,
            "display_name": item.organizer.display_name, "avatar_url": item.organizer.avatar_url,
            "is_anonymous": False,
        }, "title": item.title, "description_markdown": item.description_markdown,
        "category": item.category, "location": item.location,
        "starts_at": item.starts_at.isoformat(), "ends_at": item.ends_at.isoformat(),
        "registration_deadline": item.registration_deadline.isoformat(),
        "capacity": item.capacity, "registered_count": item.registered_count,
        "status": item.status, "my_registration_status": item.my_registration_status,
        "cancellation_reason": item.cancellation_reason,
        "moderation_case_id": str(item.moderation_case_id) if item.moderation_case_id else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "version": item.version, "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def event_response_body(item: EventData, *, request_id: str, timestamp: datetime) -> dict[str, object]:
    return {"code": "OK", "message": "success", "data": event_payload(item),
            "request_id": request_id, "timestamp": timestamp.isoformat()}


@asynccontextmanager
async def _transaction(session: AsyncSession, manage: bool):
    if manage:
        async with session.begin():
            yield
    else:
        yield
