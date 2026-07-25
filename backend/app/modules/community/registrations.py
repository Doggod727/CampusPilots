from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from typing import Callable
from uuid import UUID

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import (
    EventCapacityFull, EventNotFound, EventRegistrationBusy,
    EventRegistrationClosed, EventRegistrationNotFound,
)
from app.modules.community.models import EventRegistration
from app.modules.community.posts import PublicAuthorData
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import EventRepository
from app.modules.platform.auth import AuthenticatedUser, PermissionDenied
from app.modules.platform.audit import AuditService
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService


@dataclass(frozen=True)
class RegistrationData:
    event_id: UUID
    participant: PublicAuthorData
    status: str
    registered_at: datetime
    cancelled_at: datetime | None
    event_registered_count: int


@dataclass(frozen=True)
class RegistrationPageData:
    items: tuple[RegistrationData, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class RegistrationMutationResult:
    status_code: int
    body: dict[str, object] = field(repr=False)


class EventRegistrationService:
    def __init__(
        self, *, session: AsyncSession, repository: EventRepository,
        profiles: PublicUserProfilePort, idempotency: IdempotencyService,
        audit: AuditService, now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session, self._repository, self._profiles = session, repository, profiles
        self._idempotency, self._audit = idempotency, audit
        self._now = now or (lambda: datetime.now(UTC))

    async def list(
        self, *, actor: AuthenticatedUser, event_id: UUID, page: int, page_size: int,
    ) -> RegistrationPageData:
        event = await self._repository.get_visible(
            event_id=event_id, user_id=actor.user_id,
            moderator="community:moderate" in actor.permissions, now=self._time(),
        )
        if event is None:
            raise EventNotFound()
        if event.organizer_user_id != actor.user_id and "community:moderate" not in actor.permissions:
            raise PermissionDenied()
        rows, total = await self._repository.list_registrations(
            event_id=event_id, page=page, page_size=page_size,
        )
        profiles = await self._profiles.get_many({row.user_id for row in rows})
        return RegistrationPageData(tuple(self._data(row, event.registered_count, profiles) for row in rows),
                                    page, page_size, total)

    async def register(
        self, *, actor: AuthenticatedUser, event_id: UUID, idempotency_key: str,
        request_id: str, manage_transaction: bool = True,
    ) -> RegistrationMutationResult:
        try:
            async with _transaction(self._session, manage_transaction):
                decision = await self._idempotency.begin(
                    user_id=actor.user_id, endpoint=f"POST /api/v1/events/{event_id}/registrations",
                    idempotency_key=idempotency_key, request_body={},
                )
                if decision.replay is not None:
                    return RegistrationMutationResult(decision.replay.response_status,
                                                      dict(decision.replay.response_body))
                if decision.pending:
                    raise IdempotencyConflict()
                await self._repository.set_registration_lock_timeout()
                event = await self._repository.get_for_update(event_id)
                now = self._time()
                if event is None or event.status != "published" or event.deleted_at is not None:
                    raise EventNotFound()
                if event.registration_deadline < now or event.starts_at <= now:
                    raise EventRegistrationClosed()
                row = await self._repository.get_registration_for_update(
                    event_id=event_id, user_id=actor.user_id,
                )
                changed = row is None or row.status == "cancelled"
                if changed and event.registered_count >= event.capacity:
                    raise EventCapacityFull()
                if row is None:
                    row = EventRegistration(event_id=event_id, user_id=actor.user_id,
                        status="registered", registered_at=now, cancelled_at=None, updated_at=now)
                    self._repository.add_registration(row)
                elif row.status == "cancelled":
                    row.status, row.registered_at, row.cancelled_at, row.updated_at = "registered", now, None, now
                if changed:
                    event.registered_count += 1
                    event.updated_at = now
                await self._session.flush()
                profiles = await self._profiles.get_many({actor.user_id})
                data = self._data(row, event.registered_count, profiles)
                body = registration_response_body(data, request_id=request_id, timestamp=now)
                if changed:
                    self._audit.record_success(action="community.event.register", resource_type="event",
                        resource_id=str(event_id), request_id=request_id, actor_user_id=actor.user_id,
                        actor_username=actor.username, after_data={"event_id": str(event_id), "active": True})
                if not await self._idempotency.complete(record_id=decision.record_id,
                    response_status=200, response_body=body, resource_type="event_registration",
                    resource_id=f"{event_id}:{actor.user_id}"):
                    raise IdempotencyConflict()
                return RegistrationMutationResult(200, body)
        except (OperationalError, DBAPIError) as exc:
            if "lock timeout" in str(exc).lower() or "55P03" in str(exc):
                raise EventRegistrationBusy() from None
            raise

    async def cancel(
        self, *, actor: AuthenticatedUser, event_id: UUID, request_id: str,
    ) -> RegistrationData:
        try:
            async with self._session.begin():
                await self._repository.set_registration_lock_timeout()
                event = await self._repository.get_for_update(event_id)
                now = self._time()
                if event is None or event.deleted_at is not None:
                    raise EventNotFound()
                if event.starts_at <= now:
                    raise EventRegistrationClosed()
                row = await self._repository.get_registration_for_update(event_id=event_id, user_id=actor.user_id)
                if row is None:
                    raise EventRegistrationNotFound()
                if row.status == "registered":
                    row.status, row.cancelled_at, row.updated_at = "cancelled", now, now
                    event.registered_count = max(event.registered_count - 1, 0)
                    event.updated_at = now
                    self._audit.record_success(action="community.event.registration.cancel",
                        resource_type="event", resource_id=str(event_id), request_id=request_id,
                        actor_user_id=actor.user_id, actor_username=actor.username,
                        after_data={"event_id": str(event_id), "active": False})
                await self._session.flush()
                profiles = await self._profiles.get_many({actor.user_id})
                return self._data(row, event.registered_count, profiles)
        except (OperationalError, DBAPIError) as exc:
            if "lock timeout" in str(exc).lower() or "55P03" in str(exc):
                raise EventRegistrationBusy() from None
            raise

    @staticmethod
    def _data(row: EventRegistration, count: int, profiles: dict[UUID, object]) -> RegistrationData:
        profile = profiles.get(row.user_id)
        return RegistrationData(row.event_id, PublicAuthorData(row.user_id,
            getattr(profile, "display_name", "未知用户"), getattr(profile, "avatar_url", None), False),
            row.status, row.registered_at, row.cancelled_at, count)

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)


def registration_payload(item: RegistrationData) -> dict[str, object]:
    return {"event_id": str(item.event_id), "participant": {
        "user_id": str(item.participant.user_id), "display_name": item.participant.display_name,
        "avatar_url": item.participant.avatar_url, "is_anonymous": False,
    }, "status": item.status, "registered_at": item.registered_at.isoformat(),
        "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
        "event_registered_count": item.event_registered_count}


def registration_response_body(item: RegistrationData, *, request_id: str, timestamp: datetime) -> dict[str, object]:
    return {"code": "OK", "message": "success", "data": registration_payload(item),
            "request_id": request_id, "timestamp": timestamp.isoformat()}


@asynccontextmanager
async def _transaction(session: AsyncSession, manage: bool):
    if manage:
        async with session.begin():
            yield
    else:
        yield
