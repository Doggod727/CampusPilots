from dataclasses import dataclass
from uuid import UUID

from app.core.errors import AppError
from app.modules.campus_service.guides import GuideDetailDTO, GuideMaterialRawDTO

_CONDITION_DIMENSIONS = ("campus_codes", "student_types")


class GuideMaterialConditionInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=500,
            code="GUIDE_MATERIAL_CONDITION_INVALID",
            message="办事指南材料条件配置无效",
        )


@dataclass(frozen=True)
class GuideMaterialDTO:
    id: UUID
    name: str
    description: str | None
    required: bool
    copies: int
    included: bool
    inclusion_reason: str
    sort_order: int


@dataclass(frozen=True)
class MaterialChecklistDTO:
    guide_id: UUID
    campus_code: str
    student_type: str
    applicable: bool
    applicability_reason: str | None
    materials: tuple[GuideMaterialDTO, ...]


def _validated_condition(material: GuideMaterialRawDTO) -> dict[str, list[str]]:
    condition = material.condition
    if not isinstance(condition, dict) or set(condition) - set(_CONDITION_DIMENSIONS):
        raise GuideMaterialConditionInvalid()
    validated: dict[str, list[str]] = {}
    for dimension, values in condition.items():
        if not isinstance(values, list) or any(
            not isinstance(value, str) for value in values
        ):
            raise GuideMaterialConditionInvalid()
        validated[dimension] = values
    return validated


class MaterialChecklistService:
    def build_checklist(
        self,
        detail: GuideDetailDTO,
        *,
        campus_code: str,
        student_type: str,
    ) -> MaterialChecklistDTO:
        materials = tuple(
            self._evaluate_material(
                material,
                campus_code=campus_code,
                student_type=student_type,
            )
            for material in detail.materials
        )
        return MaterialChecklistDTO(
            guide_id=detail.summary.id,
            campus_code=campus_code,
            student_type=student_type,
            applicable=detail.applicability.applicable,
            applicability_reason=detail.applicability.notes,
            materials=materials,
        )

    def _evaluate_material(
        self,
        material: GuideMaterialRawDTO,
        *,
        campus_code: str,
        student_type: str,
    ) -> GuideMaterialDTO:
        condition = _validated_condition(material)
        if not condition:
            included = True
            reason = "通用材料"
        else:
            matches: list[bool] = []
            reasons: list[str] = []
            for dimension, label, actual in (
                ("campus_codes", "校区", campus_code),
                ("student_types", "学生类型", student_type),
            ):
                if dimension not in condition:
                    continue
                matched = actual in condition[dimension]
                matches.append(matched)
                reasons.append(f"{label}{'匹配' if matched else '不匹配'}：{actual}")
            included = all(matches)
            reason = "；".join(reasons)
        return GuideMaterialDTO(
            id=material.id,
            name=material.name,
            description=material.description,
            required=material.required,
            copies=material.copies,
            included=included,
            inclusion_reason=reason,
            sort_order=material.sort_order,
        )
