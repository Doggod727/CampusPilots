from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.community.errors import LostFoundItemNotFound
from app.modules.community.models import LostFoundItem
from app.modules.community.posts import PublicAuthorData
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import LostFoundRepository
from app.modules.platform.auth import AuthenticatedUser


def contact_hint(contact_type: str, value: str) -> str:
    if contact_type == "email" and "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}" if local else f"***@{domain}"
    if len(value) <= 4:
        return "*" * len(value)
    return f"***{value[-4:]}"


@dataclass(frozen=True)
class LostFoundItemData:
    id: UUID
    owner: PublicAuthorData
    item_type: str
    title: str
    category: str
    description: str
    occurred_at: datetime
    location: str
    contact_type: str
    contact_hint: str
    status: str
    moderation_case_id: UUID | None
    published_at: datetime | None
    completed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LostFoundItemPageData:
    items: tuple[LostFoundItemData, ...]
    page: int
    page_size: int
    total: int


class LostFoundQueryService:
    def __init__(self, repository: LostFoundRepository, profiles: PublicUserProfilePort) -> None:
        self._repository, self._profiles = repository, profiles

    async def list(
        self, *, actor: AuthenticatedUser, page: int, page_size: int,
        item_type: str | None = None, category: str | None = None,
        location: str | None = None, occurred_from: datetime | None = None,
        occurred_to: datetime | None = None, mine: bool = False,
    ) -> LostFoundItemPageData:
        result = await self._repository.list(user_id=actor.user_id, mine=mine,
            item_type=item_type, category=category, location=location,
            occurred_from=occurred_from, occurred_to=occurred_to,
            page=page, page_size=page_size)
        return LostFoundItemPageData(await self._hydrate(actor, result.items), page, page_size, result.total)

    async def get(self, *, actor: AuthenticatedUser, item_id: UUID) -> LostFoundItemData:
        item = await self._repository.get_visible(item_id=item_id, user_id=actor.user_id,
            moderator="community:moderate" in actor.permissions)
        if item is None:
            raise LostFoundItemNotFound()
        return (await self._hydrate(actor, (item,)))[0]

    async def _hydrate(self, actor: AuthenticatedUser, items: tuple[LostFoundItem, ...]) -> tuple[LostFoundItemData, ...]:
        profiles = await self._profiles.get_many({item.owner_user_id for item in items})
        moderator = "community:moderate" in actor.permissions
        output = []
        for item in items:
            profile = profiles.get(item.owner_user_id)
            owner = PublicAuthorData(item.owner_user_id,
                profile.display_name if profile else "未知用户",
                profile.avatar_url if profile else None, False)
            output.append(LostFoundItemData(item.id, owner, item.item_type, item.title,
                item.category, item.description, item.occurred_at, item.location,
                item.contact_type, item.contact_hint, item.status,
                item.moderation_case_id if moderator or item.owner_user_id == actor.user_id else None,
                item.published_at, item.completed_at, item.version, item.created_at, item.updated_at))
        return tuple(output)
