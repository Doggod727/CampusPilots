from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.errors import AppError
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
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

UserStatus = Literal["active", "disabled", "locked"]
UserSort = Literal[
    "created_at",
    "-created_at",
    "username",
    "-username",
    "last_login_at",
    "-last_login_at",
]


class RoleSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: str


class UserSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    username: str
    display_name: str
    email: str | None
    department: str | None
    status: UserStatus
    roles: list[RoleSummaryData]
    last_login_at: datetime | None
    created_at: datetime
    version: int


class PageMetaData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    page_size: int
    total: int
    total_pages: int


class UserPageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[UserSummaryData]
    pagination: PageMetaData


class UserNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="USER_NOT_FOUND",
            message="用户不存在",
        )


async def get_user_repository() -> AsyncIterator[UserRepository]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield UserRepository(session)
    finally:
        await database.dispose()


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
    return UserSummaryData(
        id=item.user.id,
        username=item.user.username,
        display_name=item.user.display_name,
        email=item.user.email,
        department=item.user.department,
        status=item.user.status,
        roles=[_role_summary(role) for role in item.roles],
        last_login_at=item.user.last_login_at,
        created_at=item.user.created_at,
        version=item.user.version,
    )


def _role_summary(role: Role) -> RoleSummaryData:
    return RoleSummaryData(id=role.id, code=role.code, name=role.name)
