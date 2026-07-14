from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.rbac_admin import RoleAdminService, role_admin_service_context
from app.modules.platform.rbac_update import (
    RoleNotFound,
    RoleUpdateService,
    role_update_service_context,
)
from app.modules.platform.rbac_permissions import (
    RolePermissionService,
    role_permission_service_context,
)
from app.modules.platform.rbac_schemas import (
    PermissionListData,
    RoleListData,
    permission_data,
    role_data,
    RoleResponse,
)
from app.modules.platform.repositories import RbacReadRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1", tags=["Roles"])


class RoleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    name: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)
    permission_ids: list[UUID]

    @field_validator("permission_ids")
    @classmethod
    def validate_unique_permissions(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("permission_ids must be unique")
        return value


class RoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)
    version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("name cannot be null")
        return value

    @model_validator(mode="after")
    def require_update_field(self) -> "RoleUpdateRequest":
        if not self.model_fields_set.intersection({"name", "description"}):
            raise ValueError("at least one role field must be provided")
        return self


class RolePermissionAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission_ids: list[UUID]
    version: int = Field(ge=1)

    @field_validator("permission_ids")
    @classmethod
    def validate_unique_permissions(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("permission_ids must be unique")
        return value


async def get_rbac_repository() -> AsyncIterator[RbacReadRepository]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield RbacReadRepository(session)
    finally:
        await database.dispose()


async def get_role_admin_service() -> AsyncIterator[RoleAdminService]:
    async with role_admin_service_context(get_settings()) as service:
        yield service


async def get_role_update_service() -> AsyncIterator[RoleUpdateService]:
    async with role_update_service_context(get_settings()) as service:
        yield service


async def get_role_permission_service() -> AsyncIterator[RolePermissionService]:
    async with role_permission_service_context(get_settings()) as service:
        yield service


@router.get(
    "/roles",
    operation_id="listRoles",
    response_model=SuccessResponse[RoleListData],
)
async def list_roles(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("role:read"))],
    repository: Annotated[RbacReadRepository, Depends(get_rbac_repository)],
) -> SuccessResponse[RoleListData]:
    items = await repository.list_roles()
    return SuccessResponse(
        data=RoleListData(items=[role_data(item) for item in items]),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/roles/{role_id}",
    operation_id="getRole",
    response_model=RoleResponse,
)
async def get_role(
    role_id: UUID,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("role:read"))],
    repository: Annotated[RbacReadRepository, Depends(get_rbac_repository)],
) -> SuccessResponse:
    item = await repository.get_role(role_id)
    if item is None:
        raise RoleNotFound()
    return SuccessResponse(
        data=role_data(item),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/roles",
    operation_id="createRole",
    status_code=201,
    response_model=RoleResponse,
)
async def create_role(
    payload: RoleCreateRequest,
    request: Request,
    current_user: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("role:write")),
    ],
    service: Annotated[RoleAdminService, Depends(get_role_admin_service)],
) -> SuccessResponse:
    result = await service.create_role(
        actor=current_user,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        permission_ids=payload.permission_ids,
        request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=role_data(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.patch(
    "/roles/{role_id}",
    operation_id="updateRole",
    response_model=RoleResponse,
)
async def update_role(
    role_id: UUID,
    payload: RoleUpdateRequest,
    request: Request,
    current_user: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("role:write")),
    ],
    service: Annotated[RoleUpdateService, Depends(get_role_update_service)],
) -> SuccessResponse:
    result = await service.update_role(
        actor=current_user,
        role_id=role_id,
        expected_version=payload.version,
        changes=payload.model_dump(exclude_unset=True, exclude={"version"}),
        request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=role_data(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.put(
    "/roles/{role_id}/permissions",
    operation_id="replaceRolePermissions",
    response_model=RoleResponse,
)
async def replace_role_permissions(
    role_id: UUID,
    payload: RolePermissionAssignmentRequest,
    request: Request,
    current_user: Annotated[
        AuthenticatedUser,
        Depends(require_permissions("role:permission:assign")),
    ],
    service: Annotated[RolePermissionService, Depends(get_role_permission_service)],
) -> SuccessResponse:
    result = await service.replace_permissions(
        actor=current_user,
        role_id=role_id,
        expected_version=payload.version,
        permission_ids=payload.permission_ids,
        request_id=request.state.request_id,
    )
    return SuccessResponse(
        data=role_data(result),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/permissions",
    operation_id="listPermissions",
    response_model=SuccessResponse[PermissionListData],
)
async def list_permissions(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("role:read"))],
    repository: Annotated[RbacReadRepository, Depends(get_rbac_repository)],
    module: Annotated[str | None, Query(max_length=50)] = None,
) -> SuccessResponse[PermissionListData]:
    permissions = await repository.list_permissions(module)
    return SuccessResponse(
        data=PermissionListData(
            items=[permission_data(permission) for permission in permissions]
        ),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
