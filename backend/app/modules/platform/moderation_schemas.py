from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.platform.models import ModerationCase
from app.shared.responses import SuccessResponse

ModerationStatus = Literal["pending", "approved", "rejected", "escalated"]
RiskLevel = Literal["low", "medium", "high", "critical"]
TargetModule = Literal["ai_knowledge", "campus_service", "community"]
ModerationSort = Literal["created_at", "-created_at", "risk_level", "-risk_level"]


class ModerationRuleHitData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    action: str
    matched_text: str | None = None


class ModerationCaseData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    target_module: TargetModule
    target_type: str
    target_id: UUID
    content_excerpt: str
    risk_level: RiskLevel
    rule_hits: list[ModerationRuleHitData]
    status: ModerationStatus
    submitted_by: UUID | None
    reviewer_id: UUID | None
    decision_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None
    version: int


class ModerationCasePageData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ModerationCaseData]
    pagination: dict[str, int]


ModerationCaseResponse = SuccessResponse[ModerationCaseData]
ModerationCasePageResponse = SuccessResponse[ModerationCasePageData]


def moderation_case_data(case: ModerationCase) -> ModerationCaseData:
    return ModerationCaseData(
        id=case.id, target_module=case.target_module, target_type=case.target_type,
        target_id=case.target_id, content_excerpt=case.content_excerpt,
        risk_level=case.risk_level, status=case.status,
        rule_hits=[
            ModerationRuleHitData(
                rule=str(hit.get("rule", "")), action=str(hit.get("action", "")),
                matched_text=None,
            )
            for hit in (case.rule_hits or [])
        ],
        submitted_by=case.submitted_by, reviewer_id=case.reviewer_id,
        decision_reason=case.decision_reason, created_at=case.created_at,
        reviewed_at=case.reviewed_at, version=case.version,
    )
