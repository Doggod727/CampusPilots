from datetime import datetime
from enum import Enum
from math import ceil
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.community.claims import ClaimContactData, ClaimData, ClaimPageData
from app.modules.community.lost_found_schemas import ContactType, LostFoundItemModel
from app.modules.community.post_schemas import PublicAuthorModel
from app.modules.community.topic_schemas import PageMetaData
from app.shared.responses import SuccessResponse


class ClaimStatus(str, Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"
    cancelled = "cancelled"
    completed = "completed"


class ClaimCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claimant_item_id: UUID | None = None
    evidence: str = Field(min_length=5, max_length=1000)


class ClaimModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    target_item: LostFoundItemModel
    claimant_item_id: UUID | None = None
    claimant: PublicAuthorModel
    evidence: str = Field(max_length=1000)
    status: ClaimStatus
    decision_reason: str | None = None
    claimant_confirmed: bool
    owner_confirmed: bool
    completed_at: datetime | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class ClaimPageDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ClaimModel]
    pagination: PageMetaData


ClaimResponse = SuccessResponse[ClaimModel]
ClaimPageResponse = SuccessResponse[ClaimPageDataModel]


class ClaimDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["verified", "rejected"]
    reason: str | None = Field(default=None, max_length=500)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def rejected_reason(self) -> "ClaimDecisionRequest":
        if self.decision == "rejected" and (self.reason is None or len(self.reason) < 2):
            raise ValueError("rejected decision requires reason")
        return self


class ClaimCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class ContactPartyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: PublicAuthorModel
    contact_type: ContactType
    contact_value: str


class ClaimContactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: UUID
    requester: ContactPartyModel
    counterpart: ContactPartyModel


ClaimContactResponse = SuccessResponse[ClaimContactModel]


def claim_model(item: ClaimData) -> ClaimModel:
    return ClaimModel.model_validate(item, from_attributes=True)


def claim_page_model(page: ClaimPageData) -> ClaimPageDataModel:
    return ClaimPageDataModel(items=[claim_model(item) for item in page.items],
        pagination=PageMetaData(page=page.page, page_size=page.page_size, total=page.total,
            total_pages=ceil(page.total / page.page_size) if page.total else 0))


def contact_model(item: ClaimContactData) -> ClaimContactModel:
    return ClaimContactModel.model_validate(item, from_attributes=True)
