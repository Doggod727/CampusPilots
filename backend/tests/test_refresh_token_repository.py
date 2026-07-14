import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import RefreshToken
from app.modules.platform.repositories import RefreshTokenRepository


def _session_with_result(*, rowcount: int = 0, token: RefreshToken | None = None) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.rowcount = rowcount
    result.scalar_one_or_none.return_value = token
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


def test_add_attaches_token_without_flushing_or_executing() -> None:
    session = _session_with_result()
    token = RefreshToken(
        jti=uuid4(),
        user_id=uuid4(),
        token_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    RefreshTokenRepository(session).add(token)

    session.add.assert_called_once_with(token)
    session.execute.assert_not_awaited()
    _assert_session_lifecycle_unchanged(session)


def test_get_by_token_hash_locks_exact_record_without_state_filters() -> None:
    token_hash = "b" * 64
    expected_token = MagicMock(spec=RefreshToken)
    session = _session_with_result(token=expected_token)

    returned = asyncio.run(
        RefreshTokenRepository(session).get_by_token_hash_for_update(token_hash)
    )

    assert returned is expected_token
    session.execute.assert_awaited_once()
    session.execute.return_value.scalar_one_or_none.assert_called_once_with()
    sql = _compiled_statement(session)
    assert f"platform.refresh_tokens.token_hash = '{token_hash}'" in sql
    assert "FOR UPDATE" in sql
    assert "revoked_at IS NULL" not in sql
    assert "platform.refresh_tokens.expires_at >" not in sql
    _assert_session_lifecycle_unchanged(session)


def test_mark_rotated_updates_only_active_token() -> None:
    jti = uuid4()
    replacement_jti = uuid4()
    revoked_at = datetime.now(UTC).replace(microsecond=0)
    session = _session_with_result(rowcount=1)

    rotated = asyncio.run(
        RefreshTokenRepository(session).mark_rotated(
            jti,
            replacement_jti,
            revoked_at,
        )
    )

    assert rotated is True
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session)
    assert f"platform.refresh_tokens.jti = '{jti}'" in sql
    assert "platform.refresh_tokens.revoked_at IS NULL" in sql
    assert f"replaced_by_jti='{replacement_jti}'" in sql
    _assert_session_lifecycle_unchanged(session)


def test_revoke_by_jti_is_idempotent_when_token_was_already_revoked() -> None:
    jti = uuid4()
    revoked_at = datetime.now(UTC).replace(microsecond=0)
    session = _session_with_result(rowcount=0)

    revoked = asyncio.run(
        RefreshTokenRepository(session).revoke_by_jti(jti, revoked_at)
    )

    assert revoked is False
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session)
    assert f"platform.refresh_tokens.jti = '{jti}'" in sql
    assert "platform.refresh_tokens.revoked_at IS NULL" in sql
    _assert_session_lifecycle_unchanged(session)


def test_revoke_all_for_user_returns_affected_active_tokens() -> None:
    user_id = uuid4()
    revoked_at = datetime.now(UTC).replace(microsecond=0)
    session = _session_with_result(rowcount=3)

    revoked_count = asyncio.run(
        RefreshTokenRepository(session).revoke_all_for_user(user_id, revoked_at)
    )

    assert revoked_count == 3
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session)
    assert f"platform.refresh_tokens.user_id = '{user_id}'" in sql
    assert "platform.refresh_tokens.revoked_at IS NULL" in sql
    _assert_session_lifecycle_unchanged(session)
