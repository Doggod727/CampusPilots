import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.models import Role, User
from app.modules.platform.repositories import UserRepository
from app.modules.platform.user_update import (
    DuplicateResource,
    LastSuperAdmin,
    ResourceVersionConflict,
    StatusChangeNotAllowed,
    UserNotFound,
    UserUpdateService,
)

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
_NO_EMAIL = object()


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


def _user(version: int = 2, status: str = "active") -> User:
    return User(
        id=uuid4(),
        username="student01",
        password_hash="password-hash",
        display_name="旧名称",
        email="student01@example.edu",
        department="计算机学院",
        status=status,
        failed_login_count=3,
        locked_until=NOW + timedelta(minutes=10),
        created_at=NOW,
        updated_at=NOW,
        version=version,
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
        permissions=("user:write",),
        last_login_at=None,
        created_at=NOW,
        version=1,
    )


def _service(
    user: User | None,
    roles: list[Role],
    *,
    count: int = 2,
    email_result=_NO_EMAIL,
    include_count: bool = False,
):
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    results = [_Scalar(user), _Rows(roles)]
    if email_result is not _NO_EMAIL:
        results.append(_Scalar(email_result))
    if include_count:
        results.append(_Scalar(count))
    results.append(MagicMock(rowcount=1))
    session.execute = AsyncMock(side_effect=results)
    repository = UserRepository(session)
    refresh = MagicMock()
    refresh.revoke_all_for_user = AsyncMock(return_value=1)
    audit = MagicMock()
    service = UserUpdateService(
        session=session,
        user_repository=repository,
        refresh_token_repository=refresh,
        audit_service=audit,
        now=lambda: NOW,
    )
    return service, session, refresh, audit


def test_update_profile_increments_version_and_audits_safe_snapshots() -> None:
    user = _user()
    role = Role(id=uuid4(), code="student", name="学生")
    service, session, refresh, audit = _service(
        user, [role], email_result=None, include_count=False
    )

    result = asyncio.run(
        service.update_user(
            actor=_actor(),
            user_id=user.id,
            expected_version=2,
            changes={"display_name": "新名称", "email": "new@example.edu"},
            request_id="user-update-request",
        )
    )

    assert result.user.display_name == "新名称"
    assert result.user.email == "new@example.edu"
    assert result.user.version == 3
    refresh.revoke_all_for_user.assert_not_called()
    kwargs = audit.record_success.call_args.kwargs
    assert kwargs["action"] == "user.update"
    assert kwargs["before_data"]["display_name"] == "旧名称"
    assert kwargs["after_data"]["display_name"] == "新名称"
    assert "password_hash" not in str(kwargs)
    session.commit.assert_not_called()
    session.rollback.assert_not_called()

    update_sql = str(
        session.execute.call_args_list[-1].args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "platform.users.version = 2" in update_sql
    assert "version=(platform.users.version + 1)" in update_sql


def test_enable_resets_login_state_and_disable_revokes_tokens() -> None:
    user = _user(status="locked")
    service, _, refresh, _ = _service(user, [], include_count=False)
    asyncio.run(
        service.update_user(
            actor=_actor(), user_id=user.id, expected_version=2,
            changes={"status": "active"}, request_id="enable-request",
        )
    )
    assert user.failed_login_count == 0
    assert user.locked_until is None
    refresh.revoke_all_for_user.assert_not_called()

    user = _user(status="active")
    service, _, refresh, _ = _service(user, [], count=2, include_count=False)
    asyncio.run(
        service.update_user(
            actor=_actor(), user_id=user.id, expected_version=2,
            changes={"status": "disabled"}, request_id="disable-request",
        )
    )
    refresh.revoke_all_for_user.assert_awaited_once_with(user.id, NOW)


def test_update_rejects_missing_user_version_email_and_locked_status() -> None:
    service, _, _, _ = _service(None, [])
    with pytest.raises(UserNotFound):
        asyncio.run(
            service.update_user(
                actor=_actor(), user_id=uuid4(), expected_version=1,
                changes={"display_name": "x"}, request_id="request-id-123",
            )
        )

    user = _user(version=3)
    service, _, _, _ = _service(user, [])
    with pytest.raises(ResourceVersionConflict):
        asyncio.run(
            service.update_user(
                actor=_actor(), user_id=user.id, expected_version=2,
                changes={"display_name": "x"}, request_id="request-id-123",
            )
        )

    user = _user()
    service, session, _, _ = _service(user, [], email_result=None)
    other = _user()
    session.execute.side_effect = [_Scalar(user), _Rows([]), _Scalar(other)]
    with pytest.raises(DuplicateResource):
        asyncio.run(
            service.update_user(
                actor=_actor(), user_id=user.id, expected_version=user.version,
                changes={"email": "other@example.edu"}, request_id="request-id-123",
            )
        )

    user = _user()
    service, _, _, _ = _service(user, [])
    with pytest.raises(StatusChangeNotAllowed):
        asyncio.run(
            service.update_user(
                actor=_actor(), user_id=user.id, expected_version=user.version,
                changes={"status": "locked"}, request_id="request-id-123",
            )
        )


def test_update_rejects_disabling_last_super_admin() -> None:
    user = _user()
    super_admin = Role(id=uuid4(), code="super_admin", name="管理员")
    service, _, refresh, _ = _service(
        user, [super_admin], count=1, include_count=True
    )
    with pytest.raises(LastSuperAdmin):
        asyncio.run(
            service.update_user(
                actor=_actor(), user_id=user.id, expected_version=user.version,
                changes={"status": "disabled"}, request_id="request-id-123",
            )
        )
    refresh.revoke_all_for_user.assert_not_called()
