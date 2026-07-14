from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.platform.audit import AuditService
from app.modules.platform.models import RefreshToken
from app.modules.platform.passwords import PasswordHasher
from app.modules.platform.repositories import (
    AuthPolicyRepository,
    RbacRepository,
    RefreshTokenRepository,
    UserAuthRepository,
    UserRepository,
)
from app.modules.platform.tokens import IssuedAccessToken, IssuedRefreshToken, TokenService


class InvalidCredentials(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message="用户名或密码错误",
        )


class AccountDisabled(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code="ACCOUNT_DISABLED",
            message="账号已被禁用",
        )


class AccountLocked(AppError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            status_code=423,
            code="ACCOUNT_LOCKED",
            message="账号已被临时锁定，请稍后再试",
            headers={"Retry-After": str(max(1, retry_after_seconds))},
        )


@dataclass(frozen=True)
class AuthenticatedRole:
    role_id: UUID
    code: str
    name: str


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    username: str
    display_name: str
    email: str | None
    department: str | None
    status: str
    roles: tuple[AuthenticatedRole, ...]
    permissions: tuple[str, ...]
    last_login_at: datetime
    created_at: datetime
    version: int


@dataclass(frozen=True)
class LoginResult:
    user: AuthenticatedUser
    access_token: IssuedAccessToken
    refresh_token: IssuedRefreshToken


class AuthService:
    """Login use case orchestrated in a caller-owned async database session."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        user_repository: UserRepository,
        user_auth_repository: UserAuthRepository,
        rbac_repository: RbacRepository,
        refresh_token_repository: RefreshTokenRepository,
        auth_policy_repository: AuthPolicyRepository,
        audit_service: AuditService,
        password_hasher: PasswordHasher,
        token_service: TokenService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._user_repository = user_repository
        self._user_auth_repository = user_auth_repository
        self._rbac_repository = rbac_repository
        self._refresh_token_repository = refresh_token_repository
        self._auth_policy_repository = auth_policy_repository
        self._audit_service = audit_service
        self._password_hasher = password_hasher
        self._token_service = token_service
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def login(
        self,
        *,
        username: str,
        password: str,
        request_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> LoginResult:
        now = self._current_time()
        result: LoginResult | None = None
        failure: AppError | None = None

        async with self._session.begin():
            user = await self._user_repository.get_by_username(username)
            if user is None:
                self._password_hasher.hash(password)
                self._record_login_failure(
                    username=username,
                    request_id=request_id,
                    error_code="INVALID_CREDENTIALS",
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                failure = InvalidCredentials()
            elif user.status == "disabled":
                self._record_login_failure(
                    username=user.username,
                    request_id=request_id,
                    error_code="ACCOUNT_DISABLED",
                    user_id=user.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                failure = AccountDisabled()
            elif user.locked_until is not None and user.locked_until > now:
                self._record_login_failure(
                    username=user.username,
                    request_id=request_id,
                    error_code="ACCOUNT_LOCKED",
                    user_id=user.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                failure = AccountLocked(
                    ceil((user.locked_until - now).total_seconds())
                )
            elif not self._password_hasher.verify(user.password_hash, password):
                policy = await self._auth_policy_repository.get_login_policy()
                await self._user_auth_repository.record_failed_login(
                    user.id,
                    policy.max_failed_logins,
                    now + timedelta(minutes=policy.lock_minutes),
                )
                self._record_login_failure(
                    username=user.username,
                    request_id=request_id,
                    error_code="INVALID_CREDENTIALS",
                    user_id=user.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                failure = InvalidCredentials()
            else:
                await self._user_auth_repository.record_successful_login(user.id, now)
                roles = await self._rbac_repository.list_roles_for_user(user.id)
                permissions = await self._rbac_repository.list_permission_codes_for_user(
                    user.id
                )
                role_codes = tuple(role.code for role in roles)
                role_summaries = tuple(
                    AuthenticatedRole(
                        role_id=role.id,
                        code=role.code,
                        name=role.name,
                    )
                    for role in roles
                )
                access_token = self._token_service.issue_access(
                    user_id=user.id,
                    username=user.username,
                    roles=role_codes,
                    permissions=permissions,
                )
                refresh_token = self._token_service.issue_refresh()
                self._refresh_token_repository.add(
                    RefreshToken(
                        jti=refresh_token.jti,
                        user_id=user.id,
                        token_hash=refresh_token.token_hash,
                        expires_at=refresh_token.expires_at,
                        created_ip=ip_address,
                        user_agent=user_agent,
                    )
                )
                self._audit_service.record_success(
                    action="auth.login",
                    resource_type="user",
                    resource_id=str(user.id),
                    request_id=request_id,
                    actor_user_id=user.id,
                    actor_username=user.username,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    after_data={"status": "active"},
                )
                result = LoginResult(
                    user=AuthenticatedUser(
                        user_id=user.id,
                        username=user.username,
                        display_name=user.display_name,
                        email=user.email,
                        department=user.department,
                        status="active",
                        roles=role_summaries,
                        permissions=tuple(permissions),
                        last_login_at=now,
                        created_at=user.created_at,
                        version=user.version,
                    ),
                    access_token=access_token,
                    refresh_token=refresh_token,
                )

        if failure is not None:
            raise failure
        if result is None:
            raise RuntimeError("Login did not produce a result.")
        return result

    def _record_login_failure(
        self,
        *,
        username: str,
        request_id: str,
        error_code: str,
        user_id: UUID | None = None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self._audit_service.record_failure(
            action="auth.login",
            resource_type="user",
            resource_id=username,
            request_id=request_id,
            error_code=error_code,
            actor_user_id=user_id,
            actor_username=username if user_id is not None else None,
            ip_address=ip_address,
            user_agent=user_agent,
            after_data={"status": "failure"},
        )

    def _current_time(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("Login clock must return a timezone-aware datetime.")
        return now.astimezone(UTC)
