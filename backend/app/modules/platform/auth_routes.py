from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthService, LoginResult, RefreshResult
from app.modules.platform.passwords import PasswordHasher
from app.modules.platform.repositories import (
    AuditLogRepository,
    AuthPolicyRepository,
    RbacRepository,
    RefreshTokenRepository,
    UserAuthRepository,
    UserRepository,
)
from app.modules.platform.tokens import IssuedAccessToken, IssuedRefreshToken, TokenService
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


class TokenData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class EmptyData(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginData(TokenData):
    model_config = ConfigDict(extra="forbid")

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


def get_frontend_origin() -> str:
    return str(get_settings().frontend_origin).rstrip("/")


def verify_cookie_origin(
    request: Request,
    frontend_origin: Annotated[str, Depends(get_frontend_origin)],
) -> None:
    if request.headers.get("Origin", "").rstrip("/") != frontend_origin.rstrip("/"):
        raise HTTPException(status_code=403)


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
    _set_refresh_cookie(
        response,
        result.refresh_token,
        secure=refresh_cookie_secure,
    )
    return SuccessResponse(
        data=_login_data(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/refresh",
    operation_id="refreshAccessToken",
    response_model=SuccessResponse[TokenData],
    dependencies=[Depends(verify_cookie_origin)],
)
async def refresh(
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    refresh_cookie_secure: Annotated[bool, Depends(get_refresh_cookie_secure)],
) -> SuccessResponse[TokenData]:
    result = await auth_service.refresh(
        refresh_token=request.cookies.get("refresh_token", ""),
        request_id=request.state.request_id,
        ip_address=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("User-Agent"),
    )
    _set_refresh_cookie(
        response,
        result.refresh_token,
        secure=refresh_cookie_secure,
    )
    return SuccessResponse(
        data=_token_data(result.access_token),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/logout",
    operation_id="logout",
    response_model=SuccessResponse[EmptyData],
    dependencies=[Depends(verify_cookie_origin)],
)
async def logout(
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    refresh_cookie_secure: Annotated[bool, Depends(get_refresh_cookie_secure)],
) -> SuccessResponse[EmptyData]:
    await auth_service.logout(
        refresh_token=request.cookies.get("refresh_token", ""),
        request_id=request.state.request_id,
        ip_address=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("User-Agent"),
    )
    _clear_refresh_cookie(response, secure=refresh_cookie_secure)
    return SuccessResponse(
        data=EmptyData(),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


def _set_refresh_cookie(
    response: Response,
    refresh_token: IssuedRefreshToken,
    *,
    secure: bool,
) -> None:
    response.set_cookie(
        key="refresh_token",
        value=refresh_token.token,
        max_age=_refresh_cookie_max_age(refresh_token),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        key="refresh_token",
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def _login_data(result: LoginResult) -> LoginData:
    token_data = _token_data(result.access_token)
    return LoginData(
        access_token=token_data.access_token,
        expires_in=token_data.expires_in,
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


def _token_data(access_token: IssuedAccessToken) -> TokenData:
    return TokenData(
        access_token=access_token.token,
        expires_in=max(
            1,
            ceil((access_token.expires_at - datetime.now(UTC)).total_seconds()),
        ),
    )


def _refresh_cookie_max_age(refresh_token: IssuedRefreshToken) -> int:
    return max(
        1,
        ceil((refresh_token.expires_at - datetime.now(UTC)).total_seconds()),
    )
