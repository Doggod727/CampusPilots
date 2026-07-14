import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.platform.auth import (
    AccountDisabled,
    AccountLocked,
    AuthService,
    InvalidCredentials,
    InvalidRefreshToken,
    RefreshTokenReused,
)
from app.modules.platform.models import RefreshToken, Role, User
from app.modules.platform.repositories import AuthLoginPolicy, LoginFailureState
from app.modules.platform.tokens import IssuedAccessToken, IssuedRefreshToken

FIXED_NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class StubPasswordHasher:
    def __init__(self, verification_result: bool) -> None:
        self.verification_result = verification_result
        self.verify_calls: list[tuple[str, str]] = []
        self.hash_calls: list[str] = []

    def verify(self, password_hash: str, password: str) -> bool:
        self.verify_calls.append((password_hash, password))
        return self.verification_result

    def hash(self, password: str) -> str:
        self.hash_calls.append(password)
        return "discarded-argon2-work"


class StubTokenService:
    def __init__(self) -> None:
        self.access = IssuedAccessToken(
            token="access-token-secret",
            expires_at=FIXED_NOW + timedelta(minutes=15),
            jti=uuid4(),
        )
        self.refresh = IssuedRefreshToken(
            token="refresh-token-secret",
            jti=uuid4(),
            token_hash="a" * 64,
            expires_at=FIXED_NOW + timedelta(days=7),
        )

    def issue_access(self, **kwargs: object) -> IssuedAccessToken:
        self.access_kwargs = kwargs
        return self.access

    def issue_refresh(self) -> IssuedRefreshToken:
        return self.refresh

    @staticmethod
    def hash_refresh(token: str) -> str:
        return sha256(token.encode("utf-8")).hexdigest()


def _session() -> MagicMock:
    session = MagicMock()

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    return session


def _user(**overrides: object) -> User:
    values: dict[str, object] = {
        "id": uuid4(),
        "username": "student01",
        "password_hash": "argon2-password-hash",
        "display_name": "Student",
        "status": "active",
        "failed_login_count": 0,
        "locked_until": None,
        "email": "student01@example.edu",
        "department": "计算机学院",
        "created_at": FIXED_NOW - timedelta(days=30),
        "version": 1,
    }
    values.update(overrides)
    return User(**values)


def _service(
    *,
    user: User | None,
    password_matches: bool,
    now: datetime = FIXED_NOW,
) -> tuple[AuthService, dict[str, MagicMock], StubPasswordHasher, StubTokenService]:
    session = _session()
    user_repository = MagicMock()
    user_repository.get_by_username = AsyncMock(return_value=user)
    user_repository.get_by_id = AsyncMock(return_value=user)
    user_auth_repository = MagicMock()
    user_auth_repository.record_failed_login = AsyncMock(
        return_value=LoginFailureState(1, "active", None)
    )
    user_auth_repository.record_successful_login = AsyncMock(return_value=True)
    rbac_repository = MagicMock()
    rbac_repository.list_roles_for_user = AsyncMock(
        return_value=[
            Role(id=uuid4(), code="student", name="普通学生", description=None)
        ]
    )
    rbac_repository.list_permission_codes_for_user = AsyncMock(
        return_value=["community:read"]
    )
    refresh_token_repository = MagicMock()
    refresh_token_repository.get_by_token_hash_for_update = AsyncMock()
    refresh_token_repository.mark_rotated = AsyncMock(return_value=True)
    refresh_token_repository.revoke_all_for_user = AsyncMock(return_value=0)
    auth_policy_repository = MagicMock()
    auth_policy_repository.get_login_policy = AsyncMock(
        return_value=AuthLoginPolicy(5, 15)
    )
    audit_service = MagicMock()
    password_hasher = StubPasswordHasher(password_matches)
    token_service = StubTokenService()
    service = AuthService(
        session=session,
        user_repository=user_repository,
        user_auth_repository=user_auth_repository,
        rbac_repository=rbac_repository,
        refresh_token_repository=refresh_token_repository,
        auth_policy_repository=auth_policy_repository,
        audit_service=audit_service,
        password_hasher=password_hasher,
        token_service=token_service,
        now=lambda: now,
    )
    return (
        service,
        {
            "session": session,
            "user": user_repository,
            "user_auth": user_auth_repository,
            "rbac": rbac_repository,
            "refresh": refresh_token_repository,
            "policy": auth_policy_repository,
            "audit": audit_service,
        },
        password_hasher,
        token_service,
    )


def test_login_success_resets_state_issues_tokens_and_writes_audit() -> None:
    user = _user()
    service, dependencies, hasher, tokens = _service(
        user=user,
        password_matches=True,
    )

    result = asyncio.run(
        service.login(
            username="student01",
            password="DemoPass123!",
            request_id="request-id-123",
            ip_address="127.0.0.1",
            user_agent="test-agent",
        )
    )

    assert result.user.user_id == user.id
    assert tuple(role.code for role in result.user.roles) == ("student",)
    assert result.user.permissions == ("community:read",)
    assert "access-token-secret" not in repr(result)
    assert "refresh-token-secret" not in repr(result)
    dependencies["session"].begin.assert_called_once_with()
    dependencies["user_auth"].record_successful_login.assert_awaited_once_with(
        user.id,
        FIXED_NOW,
    )
    dependencies["refresh"].add.assert_called_once()
    stored_refresh = dependencies["refresh"].add.call_args.args[0]
    assert stored_refresh.jti == tokens.refresh.jti
    assert stored_refresh.token_hash == tokens.refresh.token_hash
    assert stored_refresh.expires_at == tokens.refresh.expires_at
    assert stored_refresh.created_ip == "127.0.0.1"
    assert stored_refresh.user_agent == "test-agent"
    dependencies["audit"].record_success.assert_called_once()
    success_arguments = dependencies["audit"].record_success.call_args.kwargs
    assert success_arguments["action"] == "auth.login"
    assert success_arguments["after_data"] == {"status": "active"}
    assert "DemoPass123!" not in repr(success_arguments)
    assert "access-token-secret" not in repr(success_arguments)
    assert "refresh-token-secret" not in repr(success_arguments)
    assert hasher.verify_calls == [(user.password_hash, "DemoPass123!")]


def test_unknown_user_performs_argon2_work_and_returns_safe_credentials_error() -> None:
    service, dependencies, hasher, _ = _service(user=None, password_matches=False)

    with pytest.raises(InvalidCredentials) as error:
        asyncio.run(
            service.login(
                username="missing-user",
                password="secret-password",
                request_id="request-id-unknown",
            )
        )

    assert error.value.status_code == 401
    assert error.value.code == "INVALID_CREDENTIALS"
    assert "secret-password" not in str(error.value)
    assert hasher.hash_calls == ["secret-password"]
    dependencies["audit"].record_failure.assert_called_once()
    assert dependencies["audit"].record_failure.call_args.kwargs["error_code"] == (
        "INVALID_CREDENTIALS"
    )
    dependencies["session"].begin.assert_called_once_with()


def test_wrong_password_records_failure_but_returns_invalid_credentials_at_threshold() -> None:
    user = _user(failed_login_count=4)
    service, dependencies, hasher, _ = _service(user=user, password_matches=False)
    dependencies["user_auth"].record_failed_login.return_value = LoginFailureState(
        5,
        "locked",
        FIXED_NOW + timedelta(minutes=15),
    )

    with pytest.raises(InvalidCredentials) as error:
        asyncio.run(
            service.login(
                username=user.username,
                password="wrong-password",
                request_id="request-id-threshold",
            )
        )

    assert error.value.status_code == 401
    assert hasher.verify_calls == [(user.password_hash, "wrong-password")]
    dependencies["policy"].get_login_policy.assert_awaited_once_with()
    dependencies["user_auth"].record_failed_login.assert_awaited_once_with(
        user.id,
        5,
        FIXED_NOW + timedelta(minutes=15),
    )
    assert dependencies["audit"].record_failure.call_args.kwargs["error_code"] == (
        "INVALID_CREDENTIALS"
    )


def test_disabled_user_is_rejected_without_password_verification() -> None:
    user = _user(status="disabled")
    service, dependencies, hasher, _ = _service(user=user, password_matches=True)

    with pytest.raises(AccountDisabled) as error:
        asyncio.run(
            service.login(
                username=user.username,
                password="unused-password",
                request_id="request-id-disabled",
            )
        )

    assert error.value.status_code == 403
    assert error.value.code == "ACCOUNT_DISABLED"
    assert hasher.verify_calls == []
    assert dependencies["audit"].record_failure.call_args.kwargs["error_code"] == (
        "ACCOUNT_DISABLED"
    )


def test_locked_user_returns_423_with_retry_after_at_least_one_second() -> None:
    user = _user(locked_until=FIXED_NOW + timedelta(milliseconds=100))
    service, dependencies, hasher, _ = _service(user=user, password_matches=True)

    with pytest.raises(AccountLocked) as error:
        asyncio.run(
            service.login(
                username=user.username,
                password="unused-password",
                request_id="request-id-locked",
            )
        )

    assert error.value.status_code == 423
    assert error.value.code == "ACCOUNT_LOCKED"
    assert error.value.headers["Retry-After"] == "1"
    assert hasher.verify_calls == []
    assert dependencies["audit"].record_failure.call_args.kwargs["error_code"] == (
        "ACCOUNT_LOCKED"
    )


def test_expired_lock_allows_successful_login_and_state_reset() -> None:
    user = _user(status="locked", locked_until=FIXED_NOW - timedelta(seconds=1))
    service, dependencies, hasher, _ = _service(
        user=user,
        password_matches=True,
    )

    result = asyncio.run(
        service.login(
            username=user.username,
            password="DemoPass123!",
            request_id="request-id-expired-lock",
        )
    )

    assert result.user.user_id == user.id
    assert hasher.verify_calls == [(user.password_hash, "DemoPass123!")]
    dependencies["user_auth"].record_successful_login.assert_awaited_once_with(
        user.id,
        FIXED_NOW,
    )


def _stored_refresh_token(
    user: User,
    *,
    token: str = "presented-refresh-token",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    replaced_by_jti: object | None = None,
) -> RefreshToken:
    return RefreshToken(
        jti=uuid4(),
        user_id=user.id,
        token_hash=sha256(token.encode("utf-8")).hexdigest(),
        expires_at=expires_at or FIXED_NOW + timedelta(days=1),
        revoked_at=revoked_at,
        replaced_by_jti=replaced_by_jti,
    )


def test_refresh_rotates_active_token_and_writes_audit() -> None:
    user = _user()
    service, dependencies, _, tokens = _service(user=user, password_matches=True)
    presented_token = "presented-refresh-token"
    stored_token = _stored_refresh_token(user, token=presented_token)
    dependencies["refresh"].get_by_token_hash_for_update = AsyncMock(
        return_value=stored_token
    )
    dependencies["refresh"].mark_rotated = AsyncMock(return_value=True)

    result = asyncio.run(
        service.refresh(
            refresh_token=presented_token,
            request_id="request-id-refresh",
            ip_address="127.0.0.1",
            user_agent="refresh-agent",
        )
    )

    assert result.access_token == tokens.access
    assert result.refresh_token == tokens.refresh
    assert presented_token not in repr(result)
    dependencies["session"].begin.assert_called_once_with()
    dependencies["user"].get_by_id.assert_awaited_once_with(user.id)
    dependencies["refresh"].get_by_token_hash_for_update.assert_awaited_once_with(
        sha256(presented_token.encode("utf-8")).hexdigest()
    )
    dependencies["refresh"].mark_rotated.assert_awaited_once_with(
        stored_token.jti,
        tokens.refresh.jti,
        FIXED_NOW,
    )
    dependencies["refresh"].add.assert_called_once()
    replacement = dependencies["refresh"].add.call_args.args[0]
    assert replacement.user_id == user.id
    assert replacement.token_hash == tokens.refresh.token_hash
    assert replacement.created_ip == "127.0.0.1"
    assert replacement.user_agent == "refresh-agent"
    dependencies["audit"].record_success.assert_called_once()
    audit_arguments = dependencies["audit"].record_success.call_args.kwargs
    assert audit_arguments["action"] == "auth.refresh"
    assert audit_arguments["after_data"] == {"status": "rotated"}
    assert presented_token not in repr(audit_arguments)


def test_refresh_rejects_unknown_token_without_raw_token_in_audit() -> None:
    service, dependencies, _, _ = _service(user=None, password_matches=True)
    dependencies["refresh"].get_by_token_hash_for_update = AsyncMock(return_value=None)
    presented_token = "unknown-refresh-token"

    with pytest.raises(InvalidRefreshToken) as error:
        asyncio.run(
            service.refresh(
                refresh_token=presented_token,
                request_id="request-id-missing-refresh",
            )
        )

    assert error.value.status_code == 401
    assert error.value.code == "INVALID_REFRESH_TOKEN"
    dependencies["audit"].record_failure.assert_called_once()
    audit_arguments = dependencies["audit"].record_failure.call_args.kwargs
    assert audit_arguments["action"] == "auth.refresh"
    assert audit_arguments["error_code"] == "INVALID_REFRESH_TOKEN"
    assert presented_token not in repr(audit_arguments)
    dependencies["refresh"].revoke_all_for_user.assert_not_called()


def test_refresh_reuse_revokes_all_active_tokens_and_returns_safe_error() -> None:
    user = _user()
    service, dependencies, _, _ = _service(user=user, password_matches=True)
    stored_token = _stored_refresh_token(
        user,
        revoked_at=FIXED_NOW - timedelta(minutes=1),
        replaced_by_jti=uuid4(),
    )
    dependencies["refresh"].get_by_token_hash_for_update = AsyncMock(
        return_value=stored_token
    )

    with pytest.raises(RefreshTokenReused) as error:
        asyncio.run(
            service.refresh(
                refresh_token="reused-refresh-token",
                request_id="request-id-reused-refresh",
            )
        )

    assert error.value.status_code == 401
    assert error.value.code == "REFRESH_TOKEN_REUSED"
    dependencies["refresh"].revoke_all_for_user.assert_awaited_once_with(
        user.id,
        FIXED_NOW,
    )
    assert dependencies["audit"].record_failure.call_args.kwargs["error_code"] == (
        "REFRESH_TOKEN_REUSED"
    )


@pytest.mark.parametrize(
    ("user_status", "expires_at"),
    [
        ("active", FIXED_NOW - timedelta(seconds=1)),
        ("disabled", FIXED_NOW + timedelta(days=1)),
        ("locked", FIXED_NOW + timedelta(days=1)),
    ],
)
def test_refresh_rejects_expired_or_inactive_users_uniformly(
    user_status: str,
    expires_at: datetime,
) -> None:
    user = _user(status=user_status)
    service, dependencies, _, _ = _service(user=user, password_matches=True)
    stored_token = _stored_refresh_token(user, expires_at=expires_at)
    dependencies["refresh"].get_by_token_hash_for_update = AsyncMock(
        return_value=stored_token
    )
    dependencies["user"].get_by_id = AsyncMock(return_value=user)

    with pytest.raises(InvalidRefreshToken) as error:
        asyncio.run(
            service.refresh(
                refresh_token="invalid-or-inactive-refresh-token",
                request_id="request-id-invalid-refresh",
            )
        )

    assert error.value.status_code == 401
    assert error.value.code == "INVALID_REFRESH_TOKEN"
    assert dependencies["audit"].record_failure.call_args.kwargs["error_code"] == (
        "INVALID_REFRESH_TOKEN"
    )
    if expires_at <= FIXED_NOW:
        dependencies["user"].get_by_id.assert_not_called()
        dependencies["refresh"].revoke_all_for_user.assert_not_called()
    else:
        dependencies["refresh"].revoke_all_for_user.assert_awaited_once_with(
            user.id,
            FIXED_NOW,
        )
