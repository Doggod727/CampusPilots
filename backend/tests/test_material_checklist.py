from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.modules.campus_service.guides import (
    GuideApplicabilityDTO,
    GuideDetailDTO,
    GuideMaterialRawDTO,
    GuideSummaryDTO,
)
from app.modules.campus_service.material_checklist import (
    GuideMaterialConditionInvalid,
    MaterialChecklistService,
)
from app.modules.campus_service.reference import DepartmentDTO, GuideCategoryDTO

GUIDE_ID = UUID("40000000-0000-4000-8000-000000000001")


def _detail(*conditions: dict[str, object]) -> GuideDetailDTO:
    materials = tuple(
        GuideMaterialRawDTO(
            id=UUID(f"50000000-0000-4000-8000-{index:012d}"),
            name=f"材料 {index}",
            description=None,
            required=True,
            copies=1,
            condition=condition,
            sort_order=index * 10,
        )
        for index, condition in enumerate(conditions, 1)
    )
    return GuideDetailDTO(
        summary=GuideSummaryDTO(
            id=GUIDE_ID,
            code="guide",
            title="指南",
            summary="摘要",
            category=GuideCategoryDTO(
                id=UUID("30000000-0000-4000-8000-000000000001"),
                code="category",
                name="分类",
                sort_order=10,
            ),
            department=DepartmentDTO(
                id=UUID("10000000-0000-4000-8000-000000000001"),
                code="department",
                name="部门",
                description=None,
            ),
            location=None,
            service_hours=None,
            valid_until=date(2027, 1, 1),
            updated_at=datetime(2026, 7, 16, tzinfo=UTC),
            version=1,
        ),
        source_url=None,
        applicability=GuideApplicabilityDTO(
            campus_code="main",
            student_type="undergraduate",
            applicable=True,
            notes="主校区本科生",
        ),
        materials=materials,
        steps=(),
        contacts=(),
    )


def test_checklist_evaluates_universal_or_and_conditions_deterministically() -> None:
    result = MaterialChecklistService().build_checklist(
        _detail(
            {},
            {"campus_codes": ["east", "main"]},
            {"student_types": ["postgraduate"]},
            {
                "campus_codes": ["main"],
                "student_types": ["undergraduate", "international"],
            },
            {"campus_codes": ["east"], "student_types": ["undergraduate"]},
        ),
        campus_code="main",
        student_type="undergraduate",
    )

    assert [item.included for item in result.materials] == [True, True, False, True, False]
    assert [item.inclusion_reason for item in result.materials] == [
        "通用材料",
        "校区匹配：main",
        "学生类型不匹配：undergraduate",
        "校区匹配：main；学生类型匹配：undergraduate",
        "校区不匹配：main；学生类型匹配：undergraduate",
    ]
    assert result.applicable is True
    assert result.applicability_reason == "主校区本科生"


@pytest.mark.parametrize(
    "condition",
    [
        {"unknown": ["main"]},
        {"campus_codes": "main"},
        {"student_types": ["undergraduate", 1]},
    ],
)
def test_checklist_rejects_unknown_or_malformed_stored_conditions(
    condition: dict[str, object],
) -> None:
    with pytest.raises(GuideMaterialConditionInvalid) as exc_info:
        MaterialChecklistService().build_checklist(
            _detail(condition),
            campus_code="main",
            student_type="undergraduate",
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "GUIDE_MATERIAL_CONDITION_INVALID"
    assert "unknown" not in exc_info.value.message
