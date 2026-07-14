from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.modules.platform.auth import AuthService, AuthenticatedUser, LoginResult, RefreshResult
from app.modules.platform.auth_dependencies import (
    get_authenticated_user,
    get_auth_service,
)
from app.modules.platform.tokens import IssuedAccessToken, IssuedRefreshToken
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
    last_login_at: datetime | None
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


@router.get(
    "/me",
    operation_id="getCurrentUser",
    response_model=SuccessResponse[CurrentUserData],
)
async def get_current_user(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> SuccessResponse[CurrentUserData]:
    return SuccessResponse(
        data=_current_user_data(user),
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
        user=_current_user_data(result.user),
    )


def _current_user_data(user: AuthenticatedUser) -> CurrentUserData:
    return CurrentUserData(
        id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        department=user.department,
        status=user.status,
        roles=[
            RoleData(id=role.role_id, code=role.code, name=role.name)
            for role in user.roles
        ],
        permissions=list(user.permissions),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        version=user.version,
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
