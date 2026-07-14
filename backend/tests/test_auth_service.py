import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.platform.auth import (
    AccountDisabled,
    AccountLocked,
    AuthService,
    InvalidCredentials,
)
from app.modules.platform.models import Role, User
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
    user_auth_repository = MagicMock()
    user_auth_repository.record_failed_login = AsyncMock(
        return_value=LoginFailureState(1, "active", None)
    )
    user_auth_repository.record_successful_login = AsyncMock(return_value=True)
    rbac_repository = MagicMock()
    rbac_repository.list_roles_for_user = AsyncMock(
        return_value=[MagicMock(spec=Role, code="student")]
    )
    rbac_repository.list_permission_codes_for_user = AsyncMock(
        return_value=["community:read"]
    )
    refresh_token_repository = MagicMock()
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
    assert result.user.roles == ("student",)
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
