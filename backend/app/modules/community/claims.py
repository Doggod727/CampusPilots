from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.encryption import CommunityCipher
from app.modules.community.errors import (
    LostFoundClaimConflict, LostFoundClaimInvalid, LostFoundClaimNotFound,
    LostFoundItemNotFound,
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
