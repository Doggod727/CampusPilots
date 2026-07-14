import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.platform.models import Role, User
from app.modules.platform.repositories import UserListQuery, UserRepository


class _CountResult:
    def __init__(self, total: int) -> None:
        self._total = total

    def scalar_one(self) -> int:
        return self._total


class _UsersResult:
    def __init__(self, users: list[User]) -> None:
        self._users = users

    def scalars(self) -> "_UsersResult":
        return self

    def all(self) -> list[User]:
        return self._users


class _RolesResult:
    def __init__(self, rows: list[tuple[object, Role]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, Role]]:
        return self._rows


class _UserResult:
    def __init__(self, user: User | None) -> None:
        self._user = user

    def scalar_one_or_none(self) -> User | None:
        return self._user


class _RoleEntitiesResult:
    def __init__(self, roles: list[Role]) -> None:
        self._roles = roles

    def scalars(self) -> "_RoleEntitiesResult":
        return self

    def all(self) -> list[Role]:
        return self._roles


def _user(**overrides: object) -> User:
    values: dict[str, object] = {
        "id": uuid4(),
        "username": "student01",
        "password_hash": "not-returned",
        "display_name": "张同学",
        "email": "student01@example.edu",
        "department": "计算机学院",
        "status": "active",
        "failed_login_count": 0,
        "last_login_at": datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
        "created_at": datetime(2026, 7, 1, 8, 0, tzinfo=UTC),
        "version": 1,
    }
    values.update(overrides)
    return User(**values)


def _repository(*results: object) -> tuple[UserRepository, MagicMock]:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    return UserRepository(session), session


def _sql(session: MagicMock, call_index: int) -> str:
    statement = session.execute.call_args_list[call_index].args[0]
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_list_page_filters_sorts_and_groups_roles_without_n_plus_one() -> None:
    first = _user()
    second = _user(username="student02")
    student_role = Role(id=uuid4(), code="student", name="普通学生")
    operator_role = Role(id=uuid4(), code="community_operator", name="社区运营")
    repository, session = _repository(
        _CountResult(2),
        _UsersResult([first, second]),
        _RolesResult(
            [
                (first.id, student_role),
                (second.id, operator_role),
                (second.id, student_role),
            ]
        ),
    )
    role_filter = uuid4()

    result = asyncio.run(
        repository.list_page(
            UserListQuery(
                page=2,
                page_size=10,
                q="student",
                status="active",
                role_id=role_filter,
                sort="-last_login_at",
            )
        )
    )

    assert result.total == 2
    assert [item.user.id for item in result.items] == [first.id, second.id]
    assert [role.code for role in result.items[1].roles] == [
        "community_operator",
        "student",
    ]
    assert session.execute.await_count == 3
    count_sql = _sql(session, 0)
    users_sql = _sql(session, 1)
    roles_sql = _sql(session, 2)
    assert "platform.users.deleted_at IS NULL" in count_sql
    assert "ILIKE '%%student%%'" in count_sql
    assert f"platform.user_roles.role_id = '{role_filter}'" in count_sql
    assert "platform.users.status = 'active'" in users_sql
    assert "ORDER BY platform.users.last_login_at DESC NULLS LAST, platform.users.id ASC" in users_sql
    assert "LIMIT 10 OFFSET 10" in users_sql
    assert "JOIN platform.roles" in roles_sql
    assert "ORDER BY platform.user_roles.user_id, platform.roles.code" in roles_sql
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


@pytest.mark.parametrize(
    "sort",
    [
        "created_at",
        "-created_at",
        "username",
        "-username",
        "last_login_at",
        "-last_login_at",
    ],
)
def test_list_page_compiles_every_openapi_sort(sort: str) -> None:
    repository, session = _repository(_CountResult(0), _UsersResult([]))

    result = asyncio.run(
        repository.list_page(UserListQuery(page=1, page_size=20, sort=sort))
    )

    assert result.items == ()
    assert result.total == 0
    assert session.execute.await_count == 2
    assert "ORDER BY" in _sql(session, 1)


def test_list_page_treats_blank_query_as_no_search_filter() -> None:
    repository, session = _repository(_CountResult(0), _UsersResult([]))

    asyncio.run(
        repository.list_page(UserListQuery(page=1, page_size=20, q="   "))
    )

    assert "ILIKE" not in _sql(session, 0)


def test_get_summary_by_id_returns_active_user_and_sorted_roles() -> None:
    user = _user()
    roles = [
        Role(id=uuid4(), code="community_operator", name="社区运营"),
        Role(id=uuid4(), code="student", name="普通学生"),
    ]
    repository, session = _repository(
        _UserResult(user),
        _RoleEntitiesResult(roles),
    )

    result = asyncio.run(repository.get_summary_by_id(user.id))

    assert result is not None
    assert result.user is user
    assert result.roles == tuple(roles)
    assert session.execute.await_count == 2
    user_sql = _sql(session, 0)
    roles_sql = _sql(session, 1)
    assert "platform.users.deleted_at IS NULL" in user_sql
    assert f"platform.users.id = '{user.id}'" in user_sql
    assert "JOIN platform.user_roles" in roles_sql
    assert "ORDER BY platform.roles.code" in roles_sql
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_get_summary_by_id_does_not_query_roles_when_user_is_missing() -> None:
    repository, session = _repository(_UserResult(None))

    result = asyncio.run(repository.get_summary_by_id(uuid4()))

    assert result is None
    assert session.execute.await_count == 1
