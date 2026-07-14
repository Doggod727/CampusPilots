from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthService, LoginResult
from app.modules.platform.passwords import PasswordHasher
from app.modules.platform.repositories import (
    AuditLogRepository,
    AuthPolicyRepository,
    RbacRepository,
    RefreshTokenRepository,
    UserAuthRepository,
    UserRepository,
)
from app.modules.platform.tokens import TokenService
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class RoleData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: str


class CurrentUserData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    username: str
    display_name: str
    email: str | None
    department: str | None
    status: str
    roles: list[RoleData]
    permissions: list[str]
    last_login_at: datetime
    created_at: datetime
    version: int


class LoginData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    user: CurrentUserData


async def get_auth_service() -> AsyncIterator[AuthService]:
    settings = get_settings()
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


def get_refresh_cookie_secure() -> bool:
    return get_settings().refresh_cookie_secure


@router.post(
    "/login",
    operation_id="login",
    response_model=SuccessResponse[LoginData],
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    refresh_cookie_secure: Annotated[bool, Depends(get_refresh_cookie_secure)],
) -> SuccessResponse[LoginData]:
    result = await auth_service.login(
        username=payload.username,
        password=payload.password,
        request_id=request.state.request_id,
        ip_address=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("User-Agent"),
    )
    response.set_cookie(
        key="refresh_token",
        value=result.refresh_token.token,
        max_age=_refresh_cookie_max_age(result),
        httponly=True,
        secure=refresh_cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )
    return SuccessResponse(
        data=_login_data(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


def _login_data(result: LoginResult) -> LoginData:
    return LoginData(
        access_token=result.access_token.token,
        expires_in=max(
            1,
            ceil((result.access_token.expires_at - datetime.now(UTC)).total_seconds()),
        ),
        user=CurrentUserData(
            id=result.user.user_id,
            username=result.user.username,
            display_name=result.user.display_name,
            email=result.user.email,
            department=result.user.department,
            status=result.user.status,
            roles=[
                RoleData(id=role.role_id, code=role.code, name=role.name)
                for role in result.user.roles
            ],
            permissions=list(result.user.permissions),
            last_login_at=result.user.last_login_at,
            created_at=result.user.created_at,
            version=result.user.version,
        ),
    )


def _refresh_cookie_max_age(result: LoginResult) -> int:
    return max(
        1,
        ceil((result.refresh_token.expires_at - datetime.now(UTC)).total_seconds()),
    )
