from datetime import datetime
from enum import Enum
from math import ceil
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.community.lost_found import LostFoundItemData, LostFoundItemPageData
from app.modules.community.matcher import MatchData, MatchPageData
from app.modules.community.post_schemas import PublicAuthorModel
from app.modules.community.topic_schemas import PageMetaData
from app.shared.responses import SuccessResponse


class LostFoundItemType(str, Enum):
    lost = "lost"
    found = "found"


class ContactType(str, Enum):
    phone = "phone"
    email = "email"
    wechat = "wechat"
    other = "other"


class LostFoundStatus(str, Enum):
    pending_review = "pending_review"
    published = "published"
    claiming = "claiming"
    completed = "completed"
    closed = "closed"
    rejected = "rejected"
    deleted = "deleted"


class LostFoundItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    owner: PublicAuthorModel
    item_type: LostFoundItemType
    title: str = Field(max_length=120)
    category: str = Field(max_length=50)
    description: str = Field(max_length=2000)
    occurred_at: datetime
    location: str = Field(max_length=200)
    contact_type: ContactType
    contact_hint: str = Field(max_length=50)
    status: LostFoundStatus
    moderation_case_id: UUID | None = None
    published_at: datetime | None = None
    completed_at: datetime | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class LostFoundCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_type: LostFoundItemType
    title: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=5, max_length=2000)
    occurred_at: datetime
    location: str = Field(min_length=2, max_length=200)
    contact_type: ContactType
    contact_value: str = Field(min_length=3, max_length=200)


class LostFoundUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=2, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, min_length=5, max_length=2000)
    occurred_at: datetime | None = None
    location: str | None = Field(default=None, min_length=2, max_length=200)
    contact_type: ContactType | None = None
    contact_value: str | None = Field(default=None, min_length=3, max_length=200)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def contact_pair(self) -> "LostFoundUpdateRequest":
        if (self.contact_type is None) != (self.contact_value is None):
            raise ValueError("contact_type and contact_value must be provided together")
        return self


class LostFoundPageDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[LostFoundItemModel]
    pagination: PageMetaData


LostFoundItemResponse = SuccessResponse[LostFoundItemModel]
LostFoundPageResponse = SuccessResponse[LostFoundPageDataModel]


class LostFoundMatchReasonModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor: str
    score: float = Field(ge=0, le=1)
    explanation: str


class LostFoundMatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    source_item_id: UUID
    candidate: LostFoundItemModel
    score: float = Field(ge=0, le=1)
    reasons: list[LostFoundMatchReasonModel] = Field(min_length=4, max_length=4)
    algorithm_version: str
    created_at: datetime


class LostFoundMatchPageDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[LostFoundMatchModel]
    pagination: PageMetaData


LostFoundMatchPageResponse = SuccessResponse[LostFoundMatchPageDataModel]


def lost_found_model(item: LostFoundItemData) -> LostFoundItemModel:
    return LostFoundItemModel.model_validate(item, from_attributes=True)


def lost_found_page_model(page: LostFoundItemPageData) -> LostFoundPageDataModel:
    return LostFoundPageDataModel(items=[lost_found_model(item) for item in page.items],
        pagination=PageMetaData(page=page.page, page_size=page.page_size, total=page.total,
            total_pages=ceil(page.total / page.page_size) if page.total else 0))


def match_model(item: MatchData) -> LostFoundMatchModel:
    return LostFoundMatchModel.model_validate(item, from_attributes=True)


def match_page_model(page: MatchPageData) -> LostFoundMatchPageDataModel:
    return LostFoundMatchPageDataModel(items=[match_model(item) for item in page.items],
        pagination=PageMetaData(page=page.page, page_size=page.page_size, total=page.total,
            total_pages=ceil(page.total / page.page_size) if page.total else 0))
