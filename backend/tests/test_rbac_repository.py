import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import Role
from app.modules.platform.repositories import RbacRepository


def _session_returning(values: list[object]) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
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


def _assert_read_only(session: AsyncMock) -> None:
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_list_roles_for_user_returns_sorted_query_results() -> None:
    user_id = uuid4()
    roles = [MagicMock(spec=Role), MagicMock(spec=Role)]
    session = _session_returning(roles)

    result = asyncio.run(RbacRepository(session).list_roles_for_user(user_id))

    assert result == roles
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session)
    assert (
        "JOIN platform.user_roles ON platform.user_roles.role_id = platform.roles.id"
        in sql
    )
    assert (
        "JOIN platform.users ON platform.users.id = platform.user_roles.user_id" in sql
    )
    assert f"platform.users.id = '{user_id}'" in sql
    assert "platform.users.deleted_at IS NULL" in sql
    assert "ORDER BY platform.roles.code" in sql
    _assert_read_only(session)


def test_list_permission_codes_for_user_returns_distinct_codes() -> None:
    user_id = uuid4()
    permission_codes = ["admin:read", "user:read"]
    session = _session_returning(permission_codes)

    result = asyncio.run(
        RbacRepository(session).list_permission_codes_for_user(user_id)
    )

    assert result == permission_codes
    session.execute.assert_awaited_once()
    sql = _compiled_statement(session)
    assert "SELECT DISTINCT platform.permissions.code" in sql
    assert (
        "JOIN platform.role_permissions ON "
        "platform.role_permissions.permission_id = platform.permissions.id" in sql
    )
    assert (
        "JOIN platform.user_roles ON "
        "platform.user_roles.role_id = platform.role_permissions.role_id" in sql
    )
    assert (
        "JOIN platform.users ON platform.users.id = platform.user_roles.user_id" in sql
    )
    assert f"platform.users.id = '{user_id}'" in sql
    assert "platform.users.deleted_at IS NULL" in sql
    assert "ORDER BY platform.permissions.code" in sql
    _assert_read_only(session)


def test_rbac_queries_return_empty_lists() -> None:
    user_id = uuid4()
    roles_session = _session_returning([])
    permissions_session = _session_returning([])

    roles = asyncio.run(RbacRepository(roles_session).list_roles_for_user(user_id))
    permission_codes = asyncio.run(
        RbacRepository(permissions_session).list_permission_codes_for_user(user_id)
    )

    assert roles == []
    assert permission_codes == []
    roles_session.execute.assert_awaited_once()
    permissions_session.execute.assert_awaited_once()
    _assert_read_only(roles_session)
    _assert_read_only(permissions_session)
