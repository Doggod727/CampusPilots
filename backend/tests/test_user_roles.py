import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.models import Role, User
from app.modules.platform.repositories import UserListItem, UserRepository
from app.modules.platform.user_roles import (
    ResourceVersionConflict,
    RoleNotFound,
    UserNotFound,
    UserRoleService,
)

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class _UserResult:
    def __init__(self, user: User | None) -> None:
        self.user = user

    def scalar_one_or_none(self) -> User | None:
        return self.user


class _RolesResult:
    def __init__(self, roles: list[Role]) -> None:
        self.roles = roles

    def scalars(self) -> "_RolesResult":
        return self

    def all(self) -> list[Role]:
        return self.roles


def _user(version: int = 3) -> User:
    return User(
        id=uuid4(),
        username="student01",
        password_hash="hidden",
        display_name="学生",
        status="active",
        failed_login_count=0,
        version=version,
        created_at=NOW,
        updated_at=NOW,
    )


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        username="admin01",
        display_name="管理员",
        email=None,
        department=None,
        status="active",
        roles=(AuthenticatedRole(role_id=uuid4(), code="super_admin", name="管理员"),),
        permissions=("user:role:assign",),
        last_login_at=None,
        created_at=NOW,
        version=1,
    )


def _service(user: User | None, current_roles: list[Role], new_roles: list[Role]):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    update_result = MagicMock(rowcount=1)
    session.execute = AsyncMock(
        side_effect=[
            _UserResult(user),
            _RolesResult(current_roles),
            _RolesResult(new_roles),
            update_result,
            MagicMock(rowcount=len(current_roles)),
        ]
    )
    repository = UserRepository(session)
    audit = MagicMock()
    service = UserRoleService(
        session=session,
        user_repository=repository,
        audit_service=audit,
        now=lambda: NOW,
    )
    return service, session, audit


def test_replace_user_roles_locks_replaces_bumps_version_and_audits() -> None:
    user = _user()
    old_role = Role(id=uuid4(), code="student", name="学生")
    new_roles = [
        Role(id=uuid4(), code="community_operator", name="社区运营"),
        Role(id=uuid4(), code="student", name="学生"),
    ]
    service, session, audit = _service(user, [old_role], new_roles)

    result = asyncio.run(
        service.replace_user_roles(
            actor=_actor(),
            user_id=user.id,
            role_ids=[role.id for role in new_roles],
            expected_version=3,
            request_id="role-replace-request",
        )
    )

    assert isinstance(result, UserListItem)
    assert result.roles == tuple(new_roles)
    assert user.version == 4
    assert session.execute.await_count == 5
    assert "FOR UPDATE" in str(
        session.execute.call_args_list[0].args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    role_sql = str(
        session.execute.call_args_list[1].args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    update_sql = str(
        session.execute.call_args_list[3].args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    delete_sql = str(
        session.execute.call_args_list[4].args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "JOIN platform.user_roles" in role_sql
    assert "ORDER BY platform.roles.code" in role_sql
    assert "SET version=(platform.users.version + 1)" in update_sql
    assert "platform.users.version = 3" in update_sql
    assert "platform.user_roles.user_id" in delete_sql
    audit.record_success.assert_called_once()
    assert audit.record_success.call_args.kwargs["action"] == "user.roles.replace"
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_replace_user_roles_rejects_missing_user_or_role() -> None:
    role = Role(id=uuid4(), code="student", name="学生")
    service, _, _ = _service(None, [], [role])
    with pytest.raises(UserNotFound):
        asyncio.run(
            service.replace_user_roles(
                actor=_actor(), user_id=uuid4(), role_ids=[role.id],
                expected_version=1, request_id="request-id-123",
            )
        )

    user = _user()
    service, _, _ = _service(user, [], [])
    with pytest.raises(RoleNotFound):
        asyncio.run(
            service.replace_user_roles(
                actor=_actor(), user_id=user.id, role_ids=[uuid4()],
                expected_version=user.version, request_id="request-id-123",
            )
        )


def test_replace_user_roles_version_conflict_does_not_modify_roles() -> None:
    user = _user(version=4)
    role = Role(id=uuid4(), code="student", name="学生")
    service, session, audit = _service(user, [role], [role])

    with pytest.raises(ResourceVersionConflict):
        asyncio.run(
            service.replace_user_roles(
                actor=_actor(), user_id=user.id, role_ids=[role.id],
                expected_version=3, request_id="request-id-123",
            )
        )

    assert session.execute.await_count == 2
    audit.record_success.assert_not_called()
