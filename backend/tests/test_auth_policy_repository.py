import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.repositories import (
    AUTH_LOCK_MINUTES_KEY,
    AUTH_MAX_FAILED_LOGINS_KEY,
    AuthLoginPolicy,
    AuthPolicyRepository,
    InvalidAuthPolicy,
)


def _session_with_rows(rows: list[tuple[str, object, str]]) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.all.return_value = rows
    session.execute.return_value = result
    return session


def _assert_session_lifecycle_unchanged(session: AsyncMock) -> None:
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_get_login_policy_reads_two_valid_integer_configs_in_one_query() -> None:
    session = _session_with_rows(
        [
            (AUTH_MAX_FAILED_LOGINS_KEY, 5, "integer"),
            (AUTH_LOCK_MINUTES_KEY, 15, "integer"),
        ]
    )

    policy = asyncio.run(AuthPolicyRepository(session).get_login_policy())

    assert policy == AuthLoginPolicy(max_failed_logins=5, lock_minutes=15)
    session.execute.assert_awaited_once()
    session.execute.return_value.all.assert_called_once_with()
    statement = session.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FROM platform.app_configs" in sql
    assert "'auth.max_failed_logins'" in sql
    assert "'auth.lock_minutes'" in sql
    _assert_session_lifecycle_unchanged(session)


@pytest.mark.parametrize(
    "rows",
    [
        [(AUTH_MAX_FAILED_LOGINS_KEY, 5, "integer")],
        [
            (AUTH_MAX_FAILED_LOGINS_KEY, 5, "integer"),
            (AUTH_LOCK_MINUTES_KEY, "15", "integer"),
        ],
        [
            (AUTH_MAX_FAILED_LOGINS_KEY, 0, "integer"),
            (AUTH_LOCK_MINUTES_KEY, 15, "integer"),
        ],
        [
            (AUTH_MAX_FAILED_LOGINS_KEY, True, "integer"),
            (AUTH_LOCK_MINUTES_KEY, 15, "integer"),
        ],
        [
            (AUTH_MAX_FAILED_LOGINS_KEY, 5, "number"),
            (AUTH_LOCK_MINUTES_KEY, 15, "integer"),
        ],
        [
            (AUTH_MAX_FAILED_LOGINS_KEY, 5, "integer"),
            (AUTH_MAX_FAILED_LOGINS_KEY, 6, "integer"),
            (AUTH_LOCK_MINUTES_KEY, 15, "integer"),
        ],
    ],
)
def test_get_login_policy_rejects_missing_duplicate_or_invalid_values(
    rows: list[tuple[str, object, str]],
) -> None:
    session = _session_with_rows(rows)

    with pytest.raises(InvalidAuthPolicy, match="Authentication policy"):
        asyncio.run(AuthPolicyRepository(session).get_login_policy())

    session.execute.assert_awaited_once()
    _assert_session_lifecycle_unchanged(session)
