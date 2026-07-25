from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.campus_service.department_schemas import (
    DepartmentContactListData,
    DepartmentListData,
    DepartmentResponse,
    department_contact,
    department_detail,
    department_summary,
)
from app.modules.campus_service.reference import DepartmentService
from app.modules.campus_service.repositories import DepartmentRepository
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1", tags=["Departments"])


async def get_department_service() -> AsyncIterator[DepartmentService]:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield DepartmentService(DepartmentRepository(session))
    finally:
        await database.dispose()


@router.get(
    "/departments",
    operation_id="listDepartments",
    response_model=SuccessResponse[DepartmentListData],
)
async def list_departments(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    service: Annotated[DepartmentService, Depends(get_department_service)],
    campus_code: Annotated[str | None, Query(max_length=30)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> SuccessResponse[DepartmentListData]:
    items = await service.list_departments(q=q, campus_code=campus_code)
    return SuccessResponse(
        data=DepartmentListData(items=[department_summary(item) for item in items]),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/departments/{department_id}",
    operation_id="getDepartment",
    response_model=DepartmentResponse,
)
async def get_department(
    department_id: UUID,
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    service: Annotated[DepartmentService, Depends(get_department_service)],
    campus_code: Annotated[str | None, Query(max_length=30)] = None,
) -> DepartmentResponse:
    item = await service.get_department(department_id, campus_code=campus_code)
    return SuccessResponse(
        data=department_detail(item),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/department-contacts",
    operation_id="listDepartmentContacts",
    response_model=SuccessResponse[DepartmentContactListData],
)
async def list_department_contacts(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    service: Annotated[DepartmentService, Depends(get_department_service)],
    department_id: UUID | None = None,
    campus_code: Annotated[str | None, Query(max_length=30)] = None,
) -> SuccessResponse[DepartmentContactListData]:
    items = await service.list_contacts(
        department_id=department_id,
        campus_code=campus_code,
    )
    return SuccessResponse(
        data=DepartmentContactListData(
            items=[department_contact(item) for item in items]
        ),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
