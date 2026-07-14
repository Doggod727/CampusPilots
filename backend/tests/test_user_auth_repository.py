import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.repositories import (
    LoginFailureState,
    UserAuthRepository,
)


def _session_with_result(
    *,
    row: tuple[int, str, datetime | None] | None = None,
    rowcount: int = 0,
) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.one_or_none.return_value = row
    result.rowcount = rowcount
    session.execute.return_value = result
    return session


def _compiled_statement(session: AsyncMock) -> str:
    statement = session.execute.await_args.args[0]
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _assert_session_lifecycle_unchanged(session: AsyncMock) -> None:
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_record_failed_login_returns_atomic_updated_state() -> None:
    user_id = uuid4()
    locked_until = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=15)
    session = _session_with_result(row=(5, "locked", locked_until))

    state = asyncio.run(
        UserAuthRepository(session).record_failed_login(
            user_id,
            max_failed_logins=5,
            locked_until=locked_until,
        )
    )

    assert state == LoginFailureState(5, "locked", locked_until)
    session.execute.assert_awaited_once()
    session.execute.return_value.one_or_none.assert_called_once_with()
    sql = _compiled_statement(session)
    assert "failed_login_count=(platform.users.failed_login_count + 1)" in sql
    assert "CASE WHEN (platform.users.failed_login_count + 1 >= 5)" in sql
    assert "THEN 'locked'" in sql
    assert "RETURNING platform.users.failed_login_count, platform.users.status" in sql
    assert f"platform.users.id = '{user_id}'" in sql
    assert "platform.users.deleted_at IS NULL" in sql
    assert "platform.users.status != 'disabled'" in sql
    assert "version=" not in sql
    _assert_session_lifecycle_unchanged(session)


def test_record_failed_login_returns_none_when_no_eligible_user_exists() -> None:
    session = _session_with_result()

    state = asyncio.run(
        UserAuthRepository(session).record_failed_login(
            uuid4(),
            max_failed_logins=5,
            locked_until=datetime.now(UTC),
        )
    )

    assert state is None
    session.execute.assert_awaited_once()
    _assert_session_lifecycle_unchanged(session)


def test_record_successful_login_resets_active_or_expired_lock_state() -> None:
    user_id = uuid4()
    logged_in_at = datetime.now(UTC).replace(microsecond=0)
    session = _session_with_result(rowcount=1)

    succeeded = asyncio.run(
        UserAuthRepository(session).record_successful_login(user_id, logged_in_at)
    )

    assert succeeded is True
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session)
    assert "failed_login_count=0" in sql
    assert "locked_until=NULL" in sql
    assert "status='active'" in sql
    assert "last_login_at='" in sql
    assert f"platform.users.id = '{user_id}'" in sql
    assert "platform.users.deleted_at IS NULL" in sql
    assert "platform.users.status != 'disabled'" in sql
    assert "version=" not in sql
    _assert_session_lifecycle_unchanged(session)


def test_record_successful_login_returns_false_for_disabled_or_deleted_user() -> None:
    session = _session_with_result(rowcount=0)

    succeeded = asyncio.run(
        UserAuthRepository(session).record_successful_login(uuid4(), datetime.now(UTC))
    )

    assert succeeded is False
    session.execute.assert_awaited_once()
    _assert_session_lifecycle_unchanged(session)
