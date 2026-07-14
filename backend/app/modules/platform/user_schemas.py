from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.platform.models import Role
from app.modules.platform.repositories import UserListItem
from app.shared.responses import SuccessResponse

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


def role_summary(role: Role) -> RoleSummaryData:
    return RoleSummaryData(id=role.id, code=role.code, name=role.name)


def user_summary(item: UserListItem) -> UserSummaryData:
    return UserSummaryData(
        id=item.user.id,
        username=item.user.username,
        display_name=item.user.display_name,
        email=item.user.email,
        department=item.user.department,
        status=item.user.status,
        roles=[role_summary(role) for role in item.roles],
        last_login_at=item.user.last_login_at,
        created_at=item.user.created_at,
        version=item.user.version,
    )


UserResponse = SuccessResponse[UserSummaryData]
