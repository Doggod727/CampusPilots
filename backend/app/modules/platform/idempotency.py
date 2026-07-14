import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.platform.models import IdempotencyRecord
from app.modules.platform.repositories import IdempotencyRecordRepository

IDEMPOTENCY_TTL = timedelta(hours=24)


class IdempotencyConflict(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="IDEMPOTENCY_CONFLICT",
            message="幂等键与已有请求冲突",
        )


@dataclass(frozen=True)
class IdempotencyReplay:
    response_status: int
    response_body: Any = field(repr=False)
    resource_type: str | None
    resource_id: str | None


@dataclass(frozen=True)
class IdempotencyDecision:
    record_id: UUID
    replay: IdempotencyReplay | None = field(default=None, repr=False)
    pending: bool = False


def canonical_request_hash(request_body: object) -> str:
    """Hash JSON-compatible request data without retaining the original body."""

    encoded = json.dumps(
        request_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyService:
    """Coordinates idempotency records inside a caller-owned transaction."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: IdempotencyRecordRepository,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def begin(
        self,
        *,
        user_id: UUID,
        endpoint: str,
        idempotency_key: str,
        request_body: object,
    ) -> IdempotencyDecision:
        request_hash = canonical_request_hash(request_body)
        existing = await self._repository.get_by_scope_for_update(
            user_id,
            endpoint,
            idempotency_key,
        )
        if existing is not None:
            return self._existing_decision(existing, request_hash)

        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        record = IdempotencyRecord(
            id=uuid4(),
            user_id=user_id,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_at=now,
            expires_at=now + IDEMPOTENCY_TTL,
        )
        try:
            async with self._session.begin_nested():
                self._repository.add(record)
                await self._session.flush()
        except IntegrityError:
            winner = await self._repository.get_by_scope_for_update(
                user_id,
                endpoint,
                idempotency_key,
            )
            if winner is None:
                raise
            return self._existing_decision(winner, request_hash)

        return IdempotencyDecision(record_id=record.id)

    async def complete(
        self,
        *,
        record_id: UUID,
        response_status: int,
        response_body: object,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> bool:
        return await self._repository.complete(
            record_id,
            response_status,
            response_body,
            resource_type,
            resource_id,
        )

    @staticmethod
    def _existing_decision(
        record: IdempotencyRecord,
        request_hash: str,
    ) -> IdempotencyDecision:
        if record.request_hash != request_hash:
            raise IdempotencyConflict()
        if record.response_status is None:
            return IdempotencyDecision(record_id=record.id, pending=True)
        return IdempotencyDecision(
            record_id=record.id,
            replay=IdempotencyReplay(
                response_status=record.response_status,
                response_body=record.response_body,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
            ),
        )
