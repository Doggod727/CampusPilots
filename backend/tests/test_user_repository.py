import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.modules.platform.models import User
from app.modules.platform.repositories import UserRepository


def create_session_with_result(value: User | None) -> tuple[AsyncMock, MagicMock]:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    session.execute.return_value = result
    return session, result


def compiled_postgresql(statement: Select[tuple[User]]) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def assert_session_was_not_mutated(session: AsyncMock) -> None:
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_get_by_username_returns_active_user_with_single_query() -> None:
    expected_user = User(
        username="Student01",
        password_hash="argon2-hash",
        display_name="Student",
    )
    session, result = create_session_with_result(expected_user)
    repository = UserRepository(session)

    returned_user = asyncio.run(repository.get_by_username("Student01"))

    assert returned_user is expected_user
    session.execute.assert_awaited_once()
    result.scalar_one_or_none.assert_called_once_with()
    statement = session.execute.await_args.args[0]
    sql = compiled_postgresql(statement)
    assert "platform.users.username = 'Student01'" in sql
    assert "platform.users.deleted_at IS NULL" in sql
    assert_session_was_not_mutated(session)


def test_get_by_id_returns_none_with_soft_delete_filter() -> None:
    user_id = uuid4()
    session, result = create_session_with_result(None)
    repository = UserRepository(session)

    returned_user = asyncio.run(repository.get_by_id(user_id))

    assert returned_user is None
    session.execute.assert_awaited_once()
    result.scalar_one_or_none.assert_called_once_with()
    statement = session.execute.await_args.args[0]
    sql = compiled_postgresql(statement)
    assert f"platform.users.id = '{user_id}'" in sql
    assert "platform.users.deleted_at IS NULL" in sql
    assert_session_was_not_mutated(session)


def test_repository_method_signatures_use_uuid_for_user_id() -> None:
    annotations = UserRepository.get_by_id.__annotations__

    assert annotations["user_id"] is UUID
