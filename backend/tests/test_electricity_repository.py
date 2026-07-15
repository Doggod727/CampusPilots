import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from sqlalchemy.dialects import postgresql

from app.modules.campus_service.models import ElectricityTopupRequest
from app.modules.campus_service.repositories import ElectricityRepository

ROOM_ID = UUID("21000000-0000-4000-8000-000000000001")
USER_ID = UUID("21000000-0000-4000-8000-000000000002")


def _session_with_scalar(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_account_lookup_is_scoped_to_room_and_user() -> None:
    account = object()
    session = _session_with_scalar(account)
    repository = ElectricityRepository(session)

    assert asyncio.run(repository.get_account_for_user(ROOM_ID, USER_ID)) is account
    statement = session.execute.await_args.args[0]
    sql = _sql(statement)
    assert "electricity_accounts.room_id" in sql
    assert "electricity_account_members.user_id" in sql
    assert "JOIN campus_service.electricity_account_members" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_topup_lookup_uses_exact_scope_and_row_lock() -> None:
    request = object()
    session = _session_with_scalar(request)
    repository = ElectricityRepository(session)

    assert asyncio.run(repository.get_topup_for_update(USER_ID, "idem-1")) is request
    sql = _sql(session.execute.await_args.args[0])
    assert "requested_by" in sql and "idempotency_key" in sql
    assert "FOR UPDATE" in sql
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_add_topup_only_adds_to_caller_session() -> None:
    session = MagicMock()
    repository = ElectricityRepository(session)
    request = ElectricityTopupRequest()

    repository.add_topup(request)

    session.add.assert_called_once_with(request)
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()
