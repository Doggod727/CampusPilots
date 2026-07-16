from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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


class MaterialChecklistData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guide_id: UUID
    campus_code: str
    student_type: StudentType
    applicable: bool
    applicability_reason: str | None
    materials: list[GuideMaterialData]


MaterialChecklistResponse = SuccessResponse[MaterialChecklistData]


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
