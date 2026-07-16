from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from app.modules.community.errors import EventNotFound
from app.modules.community.models import CampusEvent
from app.modules.community.posts import PublicAuthorData
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import EventRepository
from app.modules.platform.auth import AuthenticatedUser


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
