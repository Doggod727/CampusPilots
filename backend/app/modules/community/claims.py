from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.encryption import CommunityCipher
from app.modules.community.errors import (
    LostFoundClaimConflict, LostFoundClaimInvalid, LostFoundClaimNotFound,
    LostFoundClaimStateInvalid, LostFoundItemNotFound,
)
from app.modules.community.lost_found import (
    LostFoundItemData, LostFoundQueryService, lost_found_payload,
)
from app.modules.community.models import LostFoundClaim
from app.modules.community.posts import PublicAuthorData
from app.modules.community.profiles import PublicUserProfilePort
from app.modules.community.repositories import LostFoundClaimRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService


@dataclass(frozen=True)
class ClaimData:
    id: UUID
    target_item: LostFoundItemData
    claimant_item_id: UUID | None
    claimant: PublicAuthorData
    evidence: str = field(repr=False)
    status: str = "pending"
    decision_reason: str | None = field(default=None, repr=False)
    claimant_confirmed: bool = False
    owner_confirmed: bool = False
    completed_at: datetime | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ClaimPageData:
    items: tuple[ClaimData, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class ClaimMutationResult:
    status_code: int
    body: dict[str, object] = field(repr=False)


@dataclass(frozen=True)
class ContactPartyData:
    user: PublicAuthorData
    contact_type: str
    contact_value: str = field(repr=False)


@dataclass(frozen=True)
class ClaimContactData:
    claim_id: UUID
    requester: ContactPartyData
    counterpart: ContactPartyData


class LostFoundClaimService:
    def __init__(self, *, session: AsyncSession, repository: LostFoundClaimRepository,
                 item_queries: LostFoundQueryService, profiles: PublicUserProfilePort,
                 cipher: CommunityCipher, idempotency: IdempotencyService,
                 audit: AuditService, now: Callable[[], datetime] | None = None) -> None:
        self._session, self._repository, self._item_queries = session, repository, item_queries
        self._profiles, self._cipher = profiles, cipher
        self._idempotency, self._audit = idempotency, audit
        self._now = now or (lambda: datetime.now(UTC))

    async def create(self, *, actor: AuthenticatedUser, target_id: UUID,
                     claimant_item_id: UUID | None, evidence: str,
                     idempotency_key: str, request_id: str,
                     request_body: object) -> ClaimMutationResult:
        async with self._session.begin():
            decision = await self._idempotency.begin(user_id=actor.user_id,
                endpoint=f"POST /api/v1/lost-found/{target_id}/claims",
                idempotency_key=idempotency_key, request_body=request_body)
            if decision.replay is not None:
                if not decision.replay.resource_id:
                    raise IdempotencyConflict()
                data = await self._get(actor=actor, claim_id=UUID(decision.replay.resource_id))
                meta = decision.replay.response_body
                return ClaimMutationResult(201, claim_response_body(data,
                    request_id=str(meta["request_id"]), timestamp=datetime.fromisoformat(str(meta["timestamp"]))))
            if decision.pending:
                raise IdempotencyConflict()
            target = await self._repository.get_target_for_update(target_id)
            if target is None or target.status not in {"published", "claiming"}:
                raise LostFoundItemNotFound()
            if target.owner_user_id == actor.user_id:
                raise LostFoundClaimInvalid()
            if await self._repository.active_exists(target_id=target_id, claimant_id=actor.user_id):
                raise LostFoundClaimConflict()
            if claimant_item_id is not None:
                claimant_item = await self._repository.get_claimant_item(claimant_item_id)
                if (claimant_item is None or claimant_item.id == target.id or
                    claimant_item.owner_user_id != actor.user_id or
                    claimant_item.item_type == target.item_type or
                    claimant_item.status not in {"published", "claiming"}):
                    raise LostFoundClaimInvalid()
            now = self._time()
            claim = LostFoundClaim(id=uuid4(), target_item_id=target.id,
                claimant_item_id=claimant_item_id, claimant_user_id=actor.user_id,
                evidence_ciphertext=self._cipher.encrypt(evidence), status="pending",
                decision_reason=None, decided_by=None, decided_at=None,
                claimant_confirmed_at=None, owner_confirmed_at=None, completed_at=None,
                version=1, created_at=now, updated_at=now)
            self._repository.add(claim)
            target.status, target.updated_at = "claiming", now
            await self._session.flush()
            data = await self._hydrate(actor, (claim,))
            body = claim_response_body(data[0], request_id=request_id, timestamp=now)
            self._audit.record_success(action="community.lost_found.claim.create",
                resource_type="lost_found_claim", resource_id=str(claim.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(claim.id), "target_id": str(target.id), "status": "pending"})
            safe_meta = {"request_id": request_id, "timestamp": now.isoformat(), "resource_id": str(claim.id)}
            if not await self._idempotency.complete(record_id=decision.record_id,
                response_status=201, response_body=safe_meta,
                resource_type="lost_found_claim", resource_id=str(claim.id)):
                raise IdempotencyConflict()
            return ClaimMutationResult(201, body)

    async def list(self, *, actor: AuthenticatedUser, role: str, status: str | None,
                   page: int, page_size: int) -> ClaimPageData:
        rows, total = await self._repository.list_visible(user_id=actor.user_id, role=role,
            status=status, page=page, page_size=page_size)
        return ClaimPageData(await self._hydrate(actor, rows), page, page_size, total)

    async def get(self, *, actor: AuthenticatedUser, claim_id: UUID) -> ClaimData:
        return await self._get(actor=actor, claim_id=claim_id)

    async def decide(self, *, actor: AuthenticatedUser, claim_id: UUID,
                     decision_value: str, reason: str | None, version: int,
                     idempotency_key: str, request_id: str,
                     request_body: object) -> ClaimMutationResult:
        if decision_value == "rejected" and (reason is None or not 2 <= len(reason) <= 500):
            raise LostFoundClaimInvalid()
        async with self._session.begin():
            idem = await self._idempotency.begin(user_id=actor.user_id,
                endpoint=f"POST /api/v1/lost-found-claims/{claim_id}/decision",
                idempotency_key=idempotency_key, request_body=request_body)
            if idem.replay is not None:
                data = await self._get(actor=actor, claim_id=claim_id)
                meta = idem.replay.response_body
                return ClaimMutationResult(200, claim_response_body(data,
                    request_id=str(meta["request_id"]), timestamp=datetime.fromisoformat(str(meta["timestamp"]))))
            if idem.pending:
                raise IdempotencyConflict()
            claim = await self._repository.get_visible(claim_id=claim_id,
                                                       user_id=actor.user_id, for_update=True)
            if claim is None:
                raise LostFoundClaimNotFound()
            target = await self._repository.get_target_for_update(claim.target_item_id)
            if target is None or target.owner_user_id != actor.user_id:
                raise LostFoundClaimNotFound()
            if claim.status != "pending":
                raise LostFoundClaimStateInvalid()
            if claim.version != version:
                from app.modules.community.errors import CommunityResourceVersionConflict
                raise CommunityResourceVersionConflict()
            now = self._time()
            claim.status, claim.decided_by, claim.decided_at = decision_value, actor.user_id, now
            claim.decision_reason = reason if decision_value == "rejected" else None
            claim.version += 1
            claim.updated_at = now
            if decision_value == "rejected" and not await self._repository.other_active_exists(
                    target_id=target.id, excluding=claim.id):
                target.status, target.updated_at = "published", now
            await self._session.flush()
            data = await self._hydrate(actor, (claim,))
            body = claim_response_body(data[0], request_id=request_id, timestamp=now)
            self._audit.record_success(action="community.lost_found.claim.decide",
                resource_type="lost_found_claim", resource_id=str(claim.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(claim.id), "status": claim.status, "version": claim.version})
            await self._complete_safe(idem.record_id, claim.id, request_id, now, 200)
            return ClaimMutationResult(200, body)

    async def contact(self, *, actor: AuthenticatedUser, claim_id: UUID,
                      request_id: str) -> ClaimContactData:
        claim = await self._repository.get_visible(claim_id=claim_id, user_id=actor.user_id)
        if claim is None:
            raise LostFoundClaimNotFound()
        if claim.status not in {"verified", "completed"}:
            raise LostFoundClaimStateInvalid()
        targets = await self._repository.targets_by_ids({claim.target_item_id} |
            ({claim.claimant_item_id} if claim.claimant_item_id else set()))
        target = targets.get(claim.target_item_id)
        if target is None:
            raise LostFoundClaimNotFound()
        profiles = await self._profiles.get_many({target.owner_user_id, claim.claimant_user_id})
        usernames = await getattr(self._profiles, "usernames")({claim.claimant_user_id})
        owner_party = self._item_contact(target, profiles)
        if claim.claimant_item_id and claim.claimant_item_id in targets:
            claimant_party = self._item_contact(targets[claim.claimant_item_id], profiles)
        else:
            profile = profiles.get(claim.claimant_user_id)
            claimant_party = ContactPartyData(PublicAuthorData(claim.claimant_user_id,
                profile.display_name if profile else "未知用户",
                profile.avatar_url if profile else None, False), "other",
                f"站内联系：{usernames.get(claim.claimant_user_id, 'unknown')}")
        is_owner = actor.user_id == target.owner_user_id
        result = ClaimContactData(claim.id, owner_party if is_owner else claimant_party,
                                  claimant_party if is_owner else owner_party)
        self._audit.record_success(action="community.lost_found.claim.contact",
            resource_type="lost_found_claim", resource_id=str(claim.id), request_id=request_id,
            actor_user_id=actor.user_id, actor_username=actor.username,
            after_data={"id": str(claim.id), "authorized": True})
        await self._session.commit()
        return result

    async def complete(self, *, actor: AuthenticatedUser, claim_id: UUID, version: int,
                       idempotency_key: str, request_id: str,
                       request_body: object) -> ClaimMutationResult:
        async with self._session.begin():
            idem = await self._idempotency.begin(user_id=actor.user_id,
                endpoint=f"POST /api/v1/lost-found-claims/{claim_id}/completion",
                idempotency_key=idempotency_key, request_body=request_body)
            if idem.replay is not None:
                data = await self._get(actor=actor, claim_id=claim_id)
                meta = idem.replay.response_body
                return ClaimMutationResult(200, claim_response_body(data,
                    request_id=str(meta["request_id"]), timestamp=datetime.fromisoformat(str(meta["timestamp"]))))
            if idem.pending:
                raise IdempotencyConflict()
            claim = await self._repository.get_visible(claim_id=claim_id,
                                                       user_id=actor.user_id, for_update=True)
            if claim is None:
                raise LostFoundClaimNotFound()
            if claim.status != "verified" or claim.version != version:
                if claim.status != "verified":
                    raise LostFoundClaimStateInvalid()
                from app.modules.community.errors import CommunityResourceVersionConflict
                raise CommunityResourceVersionConflict()
            items = await self._repository.items_for_update({claim.target_item_id} |
                ({claim.claimant_item_id} if claim.claimant_item_id else set()))
            target = items.get(claim.target_item_id)
            if target is None or actor.user_id not in {target.owner_user_id, claim.claimant_user_id}:
                raise LostFoundClaimNotFound()
            now = self._time()
            if actor.user_id == claim.claimant_user_id:
                claim.claimant_confirmed_at = claim.claimant_confirmed_at or now
            if actor.user_id == target.owner_user_id:
                claim.owner_confirmed_at = claim.owner_confirmed_at or now
            claim.version += 1
            claim.updated_at = now
            if claim.claimant_confirmed_at and claim.owner_confirmed_at:
                claim.status, claim.completed_at = "completed", now
                for item in items.values():
                    item.status, item.completed_at, item.updated_at = "completed", now, now
            await self._session.flush()
            data = await self._hydrate(actor, (claim,))
            body = claim_response_body(data[0], request_id=request_id, timestamp=now)
            self._audit.record_success(action="community.lost_found.claim.complete",
                resource_type="lost_found_claim", resource_id=str(claim.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                after_data={"id": str(claim.id), "status": claim.status,
                            "claimant_confirmed": claim.claimant_confirmed_at is not None,
                            "owner_confirmed": claim.owner_confirmed_at is not None})
            await self._complete_safe(idem.record_id, claim.id, request_id, now, 200)
            return ClaimMutationResult(200, body)

    async def _get(self, *, actor: AuthenticatedUser, claim_id: UUID) -> ClaimData:
        row = await self._repository.get_visible(claim_id=claim_id, user_id=actor.user_id)
        if row is None:
            raise LostFoundClaimNotFound()
        return (await self._hydrate(actor, (row,)))[0]

    async def _hydrate(self, actor: AuthenticatedUser,
                       rows: tuple[LostFoundClaim, ...]) -> tuple[ClaimData, ...]:
        targets = await self._repository.targets_by_ids({row.target_item_id for row in rows})
        target_data = await self._item_queries._hydrate(actor, tuple(targets.values()))
        target_map = {item.id: item for item in target_data}
        profiles = await self._profiles.get_many({row.claimant_user_id for row in rows})
        output = []
        for row in rows:
            profile = profiles.get(row.claimant_user_id)
            claimant = PublicAuthorData(row.claimant_user_id,
                profile.display_name if profile else "未知用户",
                profile.avatar_url if profile else None, False)
            output.append(ClaimData(row.id, target_map[row.target_item_id], row.claimant_item_id,
                claimant, self._cipher.decrypt(row.evidence_ciphertext), row.status,
                row.decision_reason, row.claimant_confirmed_at is not None,
                row.owner_confirmed_at is not None, row.completed_at, row.version,
                row.created_at, row.updated_at))
        return tuple(output)

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    def _item_contact(self, item: object, profiles: dict[UUID, object]) -> ContactPartyData:
        user_id = getattr(item, "owner_user_id")
        profile = profiles.get(user_id)
        return ContactPartyData(PublicAuthorData(user_id,
            getattr(profile, "display_name", "未知用户"), getattr(profile, "avatar_url", None), False),
            getattr(item, "contact_type"), self._cipher.decrypt(getattr(item, "contact_ciphertext")))

    async def _complete_safe(self, record_id: UUID, claim_id: UUID,
                             request_id: str, now: datetime, status: int) -> None:
        safe = {"request_id": request_id, "timestamp": now.isoformat(), "resource_id": str(claim_id)}
        if not await self._idempotency.complete(record_id=record_id, response_status=status,
            response_body=safe, resource_type="lost_found_claim", resource_id=str(claim_id)):
            raise IdempotencyConflict()


def claim_payload(item: ClaimData) -> dict[str, object]:
    return {"id": str(item.id), "target_item": lost_found_payload(item.target_item),
        "claimant_item_id": str(item.claimant_item_id) if item.claimant_item_id else None,
        "claimant": {"user_id": str(item.claimant.user_id),
            "display_name": item.claimant.display_name, "avatar_url": item.claimant.avatar_url,
            "is_anonymous": False}, "evidence": item.evidence, "status": item.status,
        "decision_reason": item.decision_reason, "claimant_confirmed": item.claimant_confirmed,
        "owner_confirmed": item.owner_confirmed,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "version": item.version, "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat()}


def claim_response_body(item: ClaimData, *, request_id: str, timestamp: datetime) -> dict[str, object]:
    return {"code": "OK", "message": "success", "data": claim_payload(item),
            "request_id": request_id, "timestamp": timestamp.isoformat()}
