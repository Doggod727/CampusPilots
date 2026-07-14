from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.rbac_schemas import (
    PermissionListData,
    RoleListData,
    permission_data,
    role_data,
)
from app.modules.platform.repositories import RbacReadRepository
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1", tags=["Roles"])


async def get_rbac_repository() -> AsyncIterator[RbacReadRepository]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield RbacReadRepository(session)
    finally:
        await database.dispose()


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
