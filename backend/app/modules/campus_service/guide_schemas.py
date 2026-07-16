from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.campus_service.department_schemas import (
    DepartmentContactData,
    DepartmentSummaryData,
    department_contact,
    department_summary,
)
from app.modules.campus_service.guides import (
    GuideApplicabilityDTO,
    GuideDetailDTO,
    GuidePageDTO,
    GuideStepDTO,
    GuideSummaryDTO,
)
from app.modules.campus_service.material_checklist import (
    GuideMaterialDTO,
    MaterialChecklistDTO,
)
from app.shared.responses import SuccessResponse

StudentType = Literal["undergraduate", "postgraduate", "international", "all"]


class GuideMaterialData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    description: str | None
    required: bool
    copies: int
    included: bool
    inclusion_reason: str
    sort_order: int


class GuideCategoryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str


class GuideApplicabilityData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campus_code: str
    student_type: StudentType
    applicable: bool
    notes: str | None


class GuideStepData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_no: int
    title: str
    description: str
    location: str | None
    estimated_minutes: int | None


class ServiceGuideSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    title: str
    summary: str
    category: GuideCategoryData
    department: DepartmentSummaryData
    location: str | None
    service_hours: str | None
    valid_until: date | None
    updated_at: datetime
    version: int


class ServiceGuideDetailData(ServiceGuideSummaryData):
    source_url: str | None
    applicability: GuideApplicabilityData
    materials: list[GuideMaterialData]
    steps: list[GuideStepData]
    contacts: list[DepartmentContactData]


class PageMetaData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    page_size: int
    total: int
    total_pages: int


class ServiceGuidePageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ServiceGuideSummaryData]
    pagination: PageMetaData


class MaterialChecklistData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guide_id: UUID
    campus_code: str
    student_type: StudentType
    applicable: bool
    applicability_reason: str | None
    materials: list[GuideMaterialData]


MaterialChecklistResponse = SuccessResponse[MaterialChecklistData]
ServiceGuidePageResponse = SuccessResponse[ServiceGuidePageData]
ServiceGuideResponse = SuccessResponse[ServiceGuideDetailData]


def guide_category(item: GuideSummaryDTO) -> GuideCategoryData:
    return GuideCategoryData(code=item.category.code, name=item.category.name)


def guide_summary(item: GuideSummaryDTO) -> ServiceGuideSummaryData:
    return ServiceGuideSummaryData(
        id=item.id,
        code=item.code,
        title=item.title,
        summary=item.summary,
        category=guide_category(item),
        department=department_summary(item.department),
        location=item.location,
        service_hours=item.service_hours,
        valid_until=item.valid_until,
        updated_at=item.updated_at,
        version=item.version,
    )


def guide_applicability(item: GuideApplicabilityDTO) -> GuideApplicabilityData:
    return GuideApplicabilityData(
        campus_code=item.campus_code,
        student_type=item.student_type,
        applicable=item.applicable,
        notes=item.notes,
    )


def guide_step(item: GuideStepDTO) -> GuideStepData:
    return GuideStepData(
        step_no=item.step_no,
        title=item.title,
        description=item.description,
        location=item.location,
        estimated_minutes=item.estimated_minutes,
    )


def service_guide_page(item: GuidePageDTO) -> ServiceGuidePageData:
    return ServiceGuidePageData(
        items=[guide_summary(summary) for summary in item.items],
        pagination=PageMetaData(
            page=item.page,
            page_size=item.page_size,
            total=item.total,
            total_pages=item.total_pages,
        ),
    )


def service_guide_detail(
    detail: GuideDetailDTO,
    materials: tuple[GuideMaterialDTO, ...],
) -> ServiceGuideDetailData:
    summary = guide_summary(detail.summary)
    return ServiceGuideDetailData(
        **summary.model_dump(),
        source_url=detail.source_url,
        applicability=guide_applicability(detail.applicability),
        materials=[guide_material(material) for material in materials],
        steps=[guide_step(step) for step in detail.steps],
        contacts=[department_contact(contact) for contact in detail.contacts],
    )


def guide_material(item: GuideMaterialDTO) -> GuideMaterialData:
    return GuideMaterialData(
        id=item.id,
        name=item.name,
        description=item.description,
        required=item.required,
        copies=item.copies,
        included=item.included,
        inclusion_reason=item.inclusion_reason,
        sort_order=item.sort_order,
    )


def material_checklist(item: MaterialChecklistDTO) -> MaterialChecklistData:
    return MaterialChecklistData(
        guide_id=item.guide_id,
        campus_code=item.campus_code,
        student_type=item.student_type,
        applicable=item.applicable,
        applicability_reason=item.applicability_reason,
        materials=[guide_material(material) for material in item.materials],
    )
