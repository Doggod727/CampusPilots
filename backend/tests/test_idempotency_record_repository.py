import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import IdempotencyRecord
from app.modules.platform.repositories import IdempotencyRecordRepository


def _session_with_result(
    *,
    rowcount: int = 0,
    record: IdempotencyRecord | None = None,
) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.rowcount = rowcount
    result.scalar_one_or_none.return_value = record
    session.execute.return_value = result
    return session


def _compiled_statement(session: AsyncMock, *, literal_binds: bool = True) -> str:
    statement = session.execute.await_args.args[0]
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": literal_binds},
        )
    )


def _assert_session_lifecycle_unchanged(session: AsyncMock) -> None:
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_add_attaches_record_without_flushing_or_executing() -> None:
    session = _session_with_result()
    record = IdempotencyRecord(
        user_id=uuid4(),
        endpoint="/api/v1/users",
        idempotency_key="create-user-key",
        request_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )

    IdempotencyRecordRepository(session).add(record)

    session.add.assert_called_once_with(record)
    session.execute.assert_not_awaited()
    _assert_session_lifecycle_unchanged(session)


def test_get_by_scope_for_update_locks_exact_record_without_expiry_filter() -> None:
    user_id = uuid4()
    expected_record = MagicMock(spec=IdempotencyRecord)
    session = _session_with_result(record=expected_record)

    returned = asyncio.run(
        IdempotencyRecordRepository(session).get_by_scope_for_update(
            user_id,
            "/api/v1/users",
            "create-user-key",
        )
    )

    assert returned is expected_record
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session)
    assert f"platform.idempotency_records.user_id = '{user_id}'" in sql
    assert "platform.idempotency_records.endpoint = '/api/v1/users'" in sql
    assert "platform.idempotency_records.idempotency_key = 'create-user-key'" in sql
    assert "FOR UPDATE" in sql
    assert "expires_at >" not in sql
    _assert_session_lifecycle_unchanged(session)


def test_complete_writes_only_the_first_response() -> None:
    record_id = uuid4()
    session = _session_with_result(rowcount=1)

    completed = asyncio.run(
        IdempotencyRecordRepository(session).complete(
            record_id,
            201,
            {"id": "resource-1"},
            "user",
            "resource-1",
        )
    )

    assert completed is True
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session, literal_binds=False)
    assert "platform.idempotency_records.id =" in sql
    assert "platform.idempotency_records.response_status IS NULL" in sql
    assert "response_status=%(response_status)s" in sql
    assert "response_body=%(response_body)s" in sql
    assert "resource_type=%(resource_type)s" in sql
    assert "resource_id=%(resource_id)s" in sql
    _assert_session_lifecycle_unchanged(session)


def test_complete_returns_false_when_response_already_exists() -> None:
    session = _session_with_result(rowcount=0)

    completed = asyncio.run(
        IdempotencyRecordRepository(session).complete(
            uuid4(),
            200,
            {"ok": True},
            None,
            None,
        )
    )

    assert completed is False
    session.execute.assert_awaited_once()
    _assert_session_lifecycle_unchanged(session)
