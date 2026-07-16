from datetime import datetime
from enum import Enum
from math import ceil
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.community.topics import TopicData, TopicPageData
from app.shared.responses import SuccessResponse


class TopicStatus(str, Enum):
    active = "active"
    archived = "archived"


class TopicDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: str
    description: str | None
    allow_anonymous: bool
    sort_order: int
    status: TopicStatus
    version: int
    created_at: datetime
    updated_at: datetime


class TopicCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9-]{2,49}$")
    name: str = Field(min_length=2, max_length=50)
    description: str | None = Field(default=None, max_length=300)
    allow_anonymous: bool = False
    sort_order: int = 0


class TopicUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=50)
    description: str | None = Field(default=None, max_length=300)
    allow_anonymous: bool | None = None
    sort_order: int | None = None
    status: TopicStatus | None = None
    version: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def reject_null_non_nullable_fields(cls, value: object) -> object:
        if isinstance(value, dict):
            for key in ("name", "allow_anonymous", "sort_order", "status"):
                if key in value and value[key] is None:
                    raise ValueError(f"{key} cannot be null")
        return value


class PageMetaData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int
    page_size: int
    total: int
    total_pages: int


class TopicPageResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TopicDataModel]
    pagination: PageMetaData


TopicResponse = SuccessResponse[TopicDataModel]
TopicPageResponse = SuccessResponse[TopicPageResponseData]


def topic_model(item: TopicData) -> TopicDataModel:
    return TopicDataModel.model_validate(item, from_attributes=True)


def topic_page_model(page: TopicPageData) -> TopicPageResponseData:
    return TopicPageResponseData(
        items=[topic_model(item) for item in page.items],
        pagination=PageMetaData(
            page=page.page, page_size=page.page_size, total=page.total,
            total_pages=ceil(page.total / page.page_size) if page.total else 0,
        ),
    )
