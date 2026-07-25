from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.encryption import CommunityCipher
from app.modules.community.errors import (
    CommunityResourceVersionConflict, LostFoundClaimInvalid, LostFoundItemNotFound,
    LostFoundStateInvalid,
)
from app.modules.community.models import LostFoundItem
from app.modules.community.posts import PublicAuthorData
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import LostFoundRepository
from app.modules.community.posts import combine_scan_results
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser, PermissionDenied
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.moderation import ModerationService


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


@dataclass(frozen=True)
class LostFoundMutationResult:
    status_code: int
    body: dict[str, object] = field(repr=False)


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


class LostFoundService:
    def __init__(
        self, *, session: AsyncSession, repository: LostFoundRepository,
        queries: LostFoundQueryService, cipher: CommunityCipher,
        moderation: ModerationService, idempotency: IdempotencyService,
        audit: AuditService, matcher: object | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session, self._repository, self._queries = session, repository, queries
        self._cipher, self._moderation = cipher, moderation
        self._idempotency, self._audit = idempotency, audit
        self._matcher = matcher
        self._now = now or (lambda: datetime.now(UTC))

    async def create(
        self, *, actor: AuthenticatedUser, item_type: str, title: str, category: str,
        description: str, occurred_at: datetime, location: str, contact_type: str,
        contact_value: str, idempotency_key: str, request_id: str, request_body: object,
        manage_transaction: bool = True,
    ) -> LostFoundMutationResult:
        async with _transaction(self._session, manage_transaction):
            decision = await self._idempotency.begin(user_id=actor.user_id,
                endpoint="POST /api/v1/lost-found", idempotency_key=idempotency_key,
                request_body=request_body)
            if decision.replay is not None:
                return LostFoundMutationResult(decision.replay.response_status,
                                               dict(decision.replay.response_body))
            if decision.pending:
                raise IdempotencyConflict()
            now = self._time()
            item = LostFoundItem(id=uuid4(), owner_user_id=actor.user_id,
                item_type=item_type, title=title, category=category, description=description,
                occurred_at=occurred_at, location=location, contact_type=contact_type,
                contact_ciphertext=self._cipher.encrypt(contact_value),
                contact_hint=contact_hint(contact_type, contact_value), status="pending_review",
                risk_level="low", moderation_case_id=None,
                moderation_policy_version="m4-sensitive-v1", published_at=None,
                completed_at=None, version=1, created_at=now, updated_at=now, deleted_at=None)
            await self._scan(item, actor=actor, request_id=request_id, now=now)
            self._repository.add(item)
            await self._session.flush()
            await self._recompute_safely(item, actor=actor, request_id=request_id)
            data = (await self._queries._hydrate(actor, (item,)))[0]
            body = lost_found_response_body(data, request_id=request_id, timestamp=now)
            self._audit.record_success(action="community.lost_found.create", resource_type="lost_found",
                resource_id=str(item.id), request_id=request_id, actor_user_id=actor.user_id,
                actor_username=actor.username,
                after_data={"id": str(item.id), "item_type": item.item_type, "status": item.status})
            if not await self._idempotency.complete(record_id=decision.record_id,
                response_status=201, response_body=body, resource_type="lost_found",
                resource_id=str(item.id)):
                raise IdempotencyConflict()
            return LostFoundMutationResult(201, body)

    async def update(
        self, *, actor: AuthenticatedUser, item_id: UUID, version: int,
        changes: dict[str, object], request_id: str,
    ) -> LostFoundItemData:
        async with self._session.begin():
            item = await self._repository.get_for_update(item_id)
            self._authorize(item, actor)
            assert item is not None
            if item.version != version:
                raise CommunityResourceVersionConflict()
            if item.status in {"completed", "closed", "deleted"}:
                raise LostFoundStateInvalid()
            for key in ("title", "category", "description", "occurred_at", "location"):
                if key in changes:
                    setattr(item, key, changes[key])
            if "contact_type" in changes and "contact_value" in changes:
                item.contact_type = str(changes["contact_type"])
                value = str(changes["contact_value"])
                item.contact_ciphertext = self._cipher.encrypt(value)
                item.contact_hint = contact_hint(item.contact_type, value)
            now = self._time()
            await self._scan(item, actor=actor, request_id=request_id, now=now)
            item.version += 1
            item.updated_at = now
            await self._session.flush()
            await self._recompute_safely(item, actor=actor, request_id=request_id)
            self._audit.record_success(action="community.lost_found.update", resource_type="lost_found",
                resource_id=str(item.id), request_id=request_id, actor_user_id=actor.user_id,
                actor_username=actor.username,
                after_data={"id": str(item.id), "status": item.status, "version": item.version})
            return (await self._queries._hydrate(actor, (item,)))[0]

    async def delete(self, *, actor: AuthenticatedUser, item_id: UUID, request_id: str) -> None:
        async with self._session.begin():
            item = await self._repository.get_for_update(item_id)
            self._authorize(item, actor)
            assert item is not None
            if await self._repository.has_active_claim(item.id):
                raise LostFoundClaimInvalid()
            now = self._time()
            item.status, item.deleted_at, item.updated_at = "deleted", now, now
            item.version += 1
            await self._session.flush()
            self._audit.record_success(action="community.lost_found.delete", resource_type="lost_found",
                resource_id=str(item.id), request_id=request_id, actor_user_id=actor.user_id,
                actor_username=actor.username, after_data={"id": str(item.id), "deleted": True})

    async def _scan(self, item: LostFoundItem, *, actor: AuthenticatedUser, request_id: str, now: datetime) -> None:
        title = await self._moderation.scan(scope="community", text=item.title)
        description = await self._moderation.scan(scope="community", text=item.description)
        combined = combine_scan_results(title, description)
        item.title, item.description = title.sanitized_text, description.sanitized_text
        item.status = {"allow": "published", "mask": "published", "review": "pending_review", "block": "rejected"}[combined.action]
        item.risk_level, item.moderation_policy_version = combined.risk_level, combined.policy_version
        item.published_at = now if item.status == "published" else None
        case = await self._moderation.submit_case(result=combined, target_module="community",
            target_type="lost_found", target_id=item.id, content=f"{item.title}\n{item.description}",
            submitted_by=actor.user_id, actor=actor, request_id=request_id)
        item.moderation_case_id = case.id if case else None

    @staticmethod
    def _authorize(item: LostFoundItem | None, actor: AuthenticatedUser) -> None:
        if item is None:
            raise LostFoundItemNotFound()
        if item.owner_user_id != actor.user_id and "community:moderate" not in actor.permissions:
            raise PermissionDenied()

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    async def _recompute_safely(
        self, item: LostFoundItem, *, actor: AuthenticatedUser, request_id: str,
    ) -> None:
        if self._matcher is None or item.status not in {"published", "claiming"}:
            return
        try:
            async with self._session.begin_nested():
                await getattr(self._matcher, "recompute")(item)
        except Exception:
            self._audit.record_success(action="community.lost_found.match_pending",
                resource_type="lost_found", resource_id=str(item.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(item.id), "recompute_required": True})


def lost_found_payload(item: LostFoundItemData) -> dict[str, object]:
    return {"id": str(item.id), "owner": {"user_id": str(item.owner.user_id),
        "display_name": item.owner.display_name, "avatar_url": item.owner.avatar_url,
        "is_anonymous": False}, "item_type": item.item_type, "title": item.title,
        "category": item.category, "description": item.description,
        "occurred_at": item.occurred_at.isoformat(), "location": item.location,
        "contact_type": item.contact_type, "contact_hint": item.contact_hint,
        "status": item.status,
        "moderation_case_id": str(item.moderation_case_id) if item.moderation_case_id else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "version": item.version, "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat()}


def lost_found_response_body(item: LostFoundItemData, *, request_id: str, timestamp: datetime) -> dict[str, object]:
    return {"code": "OK", "message": "success", "data": lost_found_payload(item),
            "request_id": request_id, "timestamp": timestamp.isoformat()}


@asynccontextmanager
async def _transaction(session: AsyncSession, manage: bool):
    if manage:
        async with session.begin():
            yield
    else:
        yield
