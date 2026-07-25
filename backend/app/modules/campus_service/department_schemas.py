from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.campus_service.reference import (
    DepartmentContactDTO,
    DepartmentDTO,
    DepartmentDetailDTO,
)
from app.shared.responses import SuccessResponse


class DepartmentContactData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    department_id: UUID
    campus_code: str
    contact_name: str | None
    office_name: str
    phone: str | None
    email: str | None
    location: str
    office_hours: str | None
    valid_from: date
    valid_until: date | None


class DepartmentSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: str
    description: str | None


class DepartmentDetailData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: str
    description: str | None
    contacts: list[DepartmentContactData]


class DepartmentListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DepartmentSummaryData]


class DepartmentContactListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DepartmentContactData]


DepartmentResponse = SuccessResponse[DepartmentDetailData]


def department_summary(item: DepartmentDTO) -> DepartmentSummaryData:
    return DepartmentSummaryData(
        id=item.id,
        code=item.code,
        name=item.name,
        description=item.description,
    )


def department_contact(item: DepartmentContactDTO) -> DepartmentContactData:
    return DepartmentContactData(
        id=item.id,
        department_id=item.department_id,
        campus_code=item.campus_code,
        contact_name=item.contact_name,
        office_name=item.office_name,
        phone=item.phone,
        email=item.email,
        location=item.location,
        office_hours=item.office_hours,
        valid_from=item.valid_from,
        valid_until=item.valid_until,
    )


def department_detail(item: DepartmentDetailDTO) -> DepartmentDetailData:
    department = item.department
    return DepartmentDetailData(
        id=department.id,
        code=department.code,
        name=department.name,
        description=department.description,
        contacts=[department_contact(contact) for contact in item.contacts],
    )
