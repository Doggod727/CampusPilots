from datetime import datetime
from enum import Enum
from math import ceil
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.community.events import EventData, EventPageData
from app.modules.community.post_schemas import PublicAuthorModel
from app.modules.community.topic_schemas import PageMetaData
from app.shared.responses import SuccessResponse


class EventStatus(str, Enum):
    pending_review = "pending_review"
    published = "published"
    rejected = "rejected"
    cancelled = "cancelled"
    ended = "ended"
    deleted = "deleted"


class RegistrationStatus(str, Enum):
    registered = "registered"
    cancelled = "cancelled"


class EventDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    organizer: PublicAuthorModel
    title: str = Field(max_length=120)
    description_markdown: str = Field(max_length=5000)
    category: str = Field(max_length=50)
    location: str = Field(max_length=200)
    starts_at: datetime
    ends_at: datetime
    registration_deadline: datetime
    capacity: int = Field(ge=1, le=10000)
    registered_count: int = Field(ge=0)
    status: EventStatus
    my_registration_status: RegistrationStatus | None = None
    cancellation_reason: str | None = None
    moderation_case_id: UUID | None = None
    published_at: datetime | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class EventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=2, max_length=120)
    description_markdown: str = Field(min_length=1, max_length=5000)
    category: str = Field(min_length=1, max_length=50)
    location: str = Field(min_length=2, max_length=200)
    starts_at: datetime
    ends_at: datetime
    registration_deadline: datetime
    capacity: int = Field(ge=1, le=10000)

    @model_validator(mode="after")
    def validate_times(self) -> "EventCreateRequest":
        if self.starts_at >= self.ends_at or self.registration_deadline > self.starts_at:
            raise ValueError("invalid event time range")
        return self


class EventUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=2, max_length=120)
    description_markdown: str | None = Field(default=None, min_length=1, max_length=5000)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    location: str | None = Field(default=None, min_length=2, max_length=200)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    registration_deadline: datetime | None = None
    capacity: int | None = Field(default=None, ge=1, le=10000)
    version: int = Field(ge=1)


class EventCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=2, max_length=500)
    version: int = Field(ge=1)


class EventPageDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[EventDataModel]
    pagination: PageMetaData


EventResponse = SuccessResponse[EventDataModel]
EventPageResponse = SuccessResponse[EventPageDataModel]


def event_model(item: EventData) -> EventDataModel:
    return EventDataModel.model_validate(item, from_attributes=True)


def event_page_model(page: EventPageData) -> EventPageDataModel:
    return EventPageDataModel(
        items=[event_model(item) for item in page.items],
        pagination=PageMetaData(page=page.page, page_size=page.page_size, total=page.total,
                                total_pages=ceil(page.total / page.page_size) if page.total else 0),
    )
