from datetime import datetime
from enum import Enum
from math import ceil
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.community.claims import ClaimData, ClaimPageData
from app.modules.community.lost_found_schemas import LostFoundItemModel
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


def claim_model(item: ClaimData) -> ClaimModel:
    return ClaimModel.model_validate(item, from_attributes=True)


def claim_page_model(page: ClaimPageData) -> ClaimPageDataModel:
    return ClaimPageDataModel(items=[claim_model(item) for item in page.items],
        pagination=PageMetaData(page=page.page, page_size=page.page_size, total=page.total,
            total_pages=ceil(page.total / page.page_size) if page.total else 0))
