from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.platform.models import Permission
from app.modules.platform.repositories import RoleListItem
from app.shared.responses import SuccessResponse


class PermissionData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: str
    module: str
    description: str | None


class RoleData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    permissions: list[PermissionData]
    user_count: int
    created_at: datetime
    updated_at: datetime
    version: int


class RoleListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RoleData]


class PermissionListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PermissionData]


RoleResponse = SuccessResponse[RoleData]


def permission_data(permission: Permission) -> PermissionData:
    return PermissionData(
        id=permission.id,
        code=permission.code,
        name=permission.name,
        module=permission.module,
        description=permission.description,
    )


def role_data(item: RoleListItem) -> RoleData:
    role = item.role
    return RoleData(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=[permission_data(permission) for permission in item.permissions],
        user_count=item.user_count,
        created_at=role.created_at,
        updated_at=role.updated_at,
        version=role.version,
    )
