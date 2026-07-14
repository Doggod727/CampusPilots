from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.models import Role
from app.modules.platform.repositories import (
    UserListItem,
    UserListPage,
    UserListQuery,
    UserRepository,
)
from app.modules.platform.user_admin import UserAdminService, user_admin_service_context
from app.modules.platform.user_roles import (
    UserNotFound,
    UserRoleService,
    user_role_service_context,
)
from app.modules.platform.user_schemas import (
    PageMetaData,
    RoleSummaryData,
    UserPageData,
    UserResponse,
    UserSort,
    UserStatus,
    UserSummaryData,
    role_summary,
    user_summary,
)
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{2,49}$")
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=50)
    email: str | None = Field(default=None, max_length=254)
    department: str | None = Field(default=None, max_length=100)
    role_ids: list[UUID] = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("value is not a valid email address")
        return value

    @field_validator("role_ids")
    @classmethod
    def validate_unique_roles(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("role_ids must be unique")
        return value


class UserRoleAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_ids: list[UUID] = Field(min_length=1)
    version: int = Field(ge=1)

    @field_validator("role_ids")
    @classmethod
    def validate_unique_roles(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("role_ids must be unique")
        return value

async def get_user_repository() -> AsyncIterator[UserRepository]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield UserRepository(session)
    finally:
        await database.dispose()


async def get_user_admin_service() -> AsyncIterator[UserAdminService]:
    async with user_admin_service_context(get_settings()) as service:
        yield service


async def get_user_role_service() -> AsyncIterator[UserRoleService]:
    async with user_role_service_context(get_settings()) as service:
        yield service


@router.get(
    "",
    operation_id="listUsers",
    response_model=SuccessResponse[UserPageData],
)
async def list_users(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("user:read"))],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: UserStatus | None = None,
    role_id: UUID | None = None,
    sort: UserSort = "-created_at",
) -> SuccessResponse[UserPageData]:
    result = await repository.list_page(
        UserListQuery(
            page=page,
            page_size=page_size,
            q=q,
            status=status,
            role_id=role_id,
            sort=sort,
        )
    )
    return SuccessResponse(
        data=_page_data(result, page=page, page_size=page_size),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "",
    operation_id="createUser",
    status_code=201,
    response_model=UserResponse,
)
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    current_user: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("user:write")),
    ],
    service: Annotated[UserAdminService, Depends(get_user_admin_service)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> JSONResponse:
    result = await service.create_user(
        actor=current_user,
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        email=payload.email,
        department=payload.department,
        role_ids=payload.role_ids,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
        request_body=payload.model_dump(mode="json"),
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers={REQUEST_ID_HEADER: result.request_id},
    )


@router.get(
    "/{user_id}",
    operation_id="getUser",
    response_model=SuccessResponse[UserSummaryData],
)
async def get_user(
    user_id: UUID,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("user:read"))],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> SuccessResponse[UserSummaryData]:
    result = await repository.get_summary_by_id(user_id)
    if result is None:
        raise UserNotFound()
    return SuccessResponse(
        data=_user_summary(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.put(
    "/{user_id}/roles",
    operation_id="replaceUserRoles",
    response_model=UserResponse,
)
async def replace_user_roles(
    user_id: UUID,
    payload: UserRoleAssignmentRequest,
    request: Request,
    current_user: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("user:role:assign")),
    ],
    service: Annotated[UserRoleService, Depends(get_user_role_service)],
) -> SuccessResponse[UserSummaryData]:
    result = await service.replace_user_roles(
        actor=current_user,
        user_id=user_id,
        role_ids=payload.role_ids,
        expected_version=payload.version,
        request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=_user_summary(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


def _page_data(result: UserListPage, *, page: int, page_size: int) -> UserPageData:
    return UserPageData(
        items=[_user_summary(item) for item in result.items],
        pagination=PageMetaData(
            page=page,
            page_size=page_size,
            total=result.total,
            total_pages=ceil(result.total / page_size),
        ),
    )


def _user_summary(item: UserListItem) -> UserSummaryData:
    return user_summary(item)


def _role_summary(role: Role) -> RoleSummaryData:
    return role_summary(role)
