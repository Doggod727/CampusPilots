from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthService, AuthenticatedUser, AuthenticationRequired
from app.modules.platform.passwords import PasswordHasher
from app.modules.platform.repositories import (
    AuditLogRepository,
    AuthPolicyRepository,
    RbacRepository,
    RefreshTokenRepository,
    UserAuthRepository,
    UserRepository,
)
from app.modules.platform.tokens import AccessClaims, InvalidAccessToken, TokenService

bearer_scheme = HTTPBearer(auto_error=False)


@asynccontextmanager
async def auth_service_context(settings: Settings) -> AsyncIterator[AuthService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield AuthService(
                session=session,
                user_repository=UserRepository(session),
                user_auth_repository=UserAuthRepository(session),
                rbac_repository=RbacRepository(session),
                refresh_token_repository=RefreshTokenRepository(session),
                auth_policy_repository=AuthPolicyRepository(session),
                audit_service=AuditService(AuditLogRepository(session)),
                password_hasher=PasswordHasher(),
                token_service=TokenService(settings),
            )
    finally:
        await database.dispose()


async def get_auth_service() -> AsyncIterator[AuthService]:
    settings = get_settings()
    async with auth_service_context(settings) as service:
        yield service


async def get_access_claims(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> AccessClaims:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationRequired()
    try:
        return TokenService(get_settings()).decode_access(credentials.credentials)
    except InvalidAccessToken:
        raise AuthenticationRequired() from None


async def get_authenticated_user(
    claims: Annotated[AccessClaims, Depends(get_access_claims)],
) -> AuthenticatedUser:
    settings = get_settings()
    async with auth_service_context(settings) as service:
        return await service.get_current_user(claims)
