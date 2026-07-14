import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.platform.idempotency import (
    IdempotencyConflict,
    IdempotencyService,
    canonical_request_hash,
)
from app.modules.platform.models import IdempotencyRecord
from app.modules.platform.repositories import IdempotencyRecordRepository


class _NestedTransaction:
    async def __aenter__(self) -> "_NestedTransaction":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def _service(
    session: AsyncMock,
    repository: MagicMock,
    *,
    now: datetime | None = None,
) -> IdempotencyService:
    session.begin_nested = MagicMock(return_value=_NestedTransaction())
    return IdempotencyService(
        session=session,
        repository=repository,
        now=(lambda: now) if now is not None else None,
    )


def _session() -> AsyncMock:
    session = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


def _repository() -> MagicMock:
    repository = MagicMock(spec=IdempotencyRecordRepository)
    repository.get_by_scope_for_update = AsyncMock(return_value=None)
    repository.complete = AsyncMock(return_value=True)
    return repository


def _record(*, request_hash: str, response_status: int | None = None) -> IdempotencyRecord:
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    return IdempotencyRecord(
        id=uuid4(),
        user_id=uuid4(),
        endpoint="/api/v1/users",
        idempotency_key="create-user-key",
        request_hash=request_hash,
        response_status=response_status,
        response_body={"access_token": "secret-token"}
        if response_status is not None
        else None,
        resource_type="user" if response_status is not None else None,
        resource_id="resource-1" if response_status is not None else None,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )


def test_canonical_hash_is_key_order_stable_but_array_order_sensitive() -> None:
    first = {"display_name": "学生", "roles": ["student", "reader"]}
    reordered = {"roles": ["student", "reader"], "display_name": "学生"}
    changed_array = {"display_name": "学生", "roles": ["reader", "student"]}

    assert canonical_request_hash(first) == canonical_request_hash(reordered)
    assert canonical_request_hash(first) != canonical_request_hash(changed_array)
    assert first == {"display_name": "学生", "roles": ["student", "reader"]}


def test_begin_creates_record_with_hash_and_24_hour_window() -> None:
    session = _session()
    repository = _repository()
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    service = _service(session, repository, now=now)
    body = {"username": "student01", "password": "not-retained"}

    decision = asyncio.run(
        service.begin(
            user_id=uuid4(),
            endpoint="/api/v1/users",
            idempotency_key="create-user-key",
            request_body=body,
        )
    )

    record = repository.add.call_args.args[0]
    assert decision.record_id == record.id
    assert decision.replay is None
    assert decision.pending is False
    assert record.request_hash == canonical_request_hash(body)
    assert record.created_at == now
    assert record.expires_at == now + timedelta(hours=24)
    assert "not-retained" not in repr(decision)
    session.flush.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_begin_replays_same_hash_without_exposing_response_in_repr() -> None:
    body = {"username": "student01"}
    record = _record(request_hash=canonical_request_hash(body), response_status=201)
    session = _session()
    repository = _repository()
    repository.get_by_scope_for_update.return_value = record

    decision = asyncio.run(
        _service(session, repository).begin(
            user_id=record.user_id,
            endpoint=record.endpoint,
            idempotency_key=record.idempotency_key,
            request_body=body,
        )
    )

    assert decision.pending is False
    assert decision.replay is not None
    assert decision.replay.response_status == 201
    assert decision.replay.response_body == {"access_token": "secret-token"}
    assert "secret-token" not in repr(decision)
    session.flush.assert_not_awaited()


def test_begin_returns_pending_for_same_hash_without_response() -> None:
    body = {"username": "student01"}
    record = _record(request_hash=canonical_request_hash(body))
    session = _session()
    repository = _repository()
    repository.get_by_scope_for_update.return_value = record

    decision = asyncio.run(
        _service(session, repository).begin(
            user_id=record.user_id,
            endpoint=record.endpoint,
            idempotency_key=record.idempotency_key,
            request_body=body,
        )
    )

    assert decision.pending is True
    assert decision.replay is None
    session.flush.assert_not_awaited()


def test_begin_rejects_different_hash_without_echoing_input() -> None:
    original = {"username": "student01"}
    changed = {"username": "admin01", "password": "super-secret"}
    record = _record(request_hash=canonical_request_hash(original), response_status=201)
    session = _session()
    repository = _repository()
    repository.get_by_scope_for_update.return_value = record

    with pytest.raises(IdempotencyConflict) as error:
        asyncio.run(
            _service(session, repository).begin(
                user_id=record.user_id,
                endpoint=record.endpoint,
                idempotency_key=record.idempotency_key,
                request_body=changed,
            )
        )

    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert "super-secret" not in str(error.value)
    assert canonical_request_hash(changed) not in str(error.value)
    session.flush.assert_not_awaited()


def test_begin_recovers_from_concurrent_unique_insert_using_savepoint() -> None:
    body = {"username": "student01"}
    winner = _record(request_hash=canonical_request_hash(body), response_status=201)
    session = _session()
    repository = _repository()
    repository.get_by_scope_for_update = AsyncMock(side_effect=[None, winner])
    session.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    decision = asyncio.run(
        _service(session, repository).begin(
            user_id=winner.user_id,
            endpoint=winner.endpoint,
            idempotency_key=winner.idempotency_key,
            request_body=body,
        )
    )

    assert decision.record_id == winner.id
    assert decision.replay is not None
    assert decision.replay.response_status == 201
    assert repository.add.call_count == 1
    assert repository.get_by_scope_for_update.await_count == 2
    assert session.flush.await_count == 1
    session.rollback.assert_not_awaited()


def test_complete_delegates_without_managing_session() -> None:
    session = _session()
    repository = _repository()
    service = _service(session, repository)
    record_id = uuid4()

    completed = asyncio.run(
        service.complete(
            record_id=record_id,
            response_status=201,
            response_body={"id": "resource-1"},
            resource_type="user",
            resource_id="resource-1",
        )
    )

    assert completed is True
    repository.complete.assert_awaited_once_with(
        record_id,
        201,
        {"id": "resource-1"},
        "user",
        "resource-1",
    )
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()
