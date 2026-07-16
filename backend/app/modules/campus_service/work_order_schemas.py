from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.campus_service.models import WorkOrder, WorkOrderEvent, WorkOrderRating
from app.shared.responses import SuccessResponse

FaultCategory = Literal[
    "electric", "plumbing", "network", "furniture", "door_window", "other"
]
WorkOrderStatus = Literal[
    "submitted", "accepted", "processing", "completed", "cancelled", "rejected"
]


class WorkOrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campus_code: str = Field(min_length=2, max_length=30)
    dormitory_area: str = Field(min_length=1, max_length=100)
    building: str = Field(min_length=1, max_length=50)
    room: str = Field(min_length=1, max_length=30)
    fault_category: FaultCategory
    description: str = Field(min_length=10, max_length=1000, repr=False)
    preferred_start_at: datetime
    preferred_end_at: datetime

    @model_validator(mode="after")
    def validate_time_window(self) -> "WorkOrderCreateRequest":
        if (
            self.preferred_start_at.tzinfo is None
            or self.preferred_end_at.tzinfo is None
            or self.preferred_end_at <= self.preferred_start_at
        ):
            raise ValueError("preferred time window must be timezone-aware and increasing")
        return self


class WorkOrderTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: Literal["accepted", "processing", "completed", "cancelled", "rejected"]
    reason: str = Field(min_length=2, max_length=500, repr=False)
    completion_note: str | None = Field(default=None, max_length=1000, repr=False)
    version: int = Field(ge=1)


class WorkOrderRatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500, repr=False)


class WorkOrderRatingData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    score: int
    comment: str | None
    created_at: datetime


class WorkOrderData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    order_no: str
    created_by: UUID
    campus_code: str
    dormitory_area: str
    building: str
    room: str
    fault_category: FaultCategory
    description: str = Field(repr=False)
    preferred_start_at: datetime
    preferred_end_at: datetime
    status: WorkOrderStatus
    assigned_to: UUID | None
    assigned_department_id: UUID | None
    rejection_reason: str | None
    completion_note: str | None
    rating: WorkOrderRatingData | None
    version: int
    submitted_at: datetime
    accepted_at: datetime | None
    processing_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    rejected_at: datetime | None
    created_at: datetime
    updated_at: datetime


WorkOrderResponse = SuccessResponse[WorkOrderData]


class PageMetaData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    page_size: int
    total: int
    total_pages: int


class WorkOrderPageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkOrderData]
    pagination: PageMetaData


class WorkOrderEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    sequence_no: int
    event_type: str
    from_status: WorkOrderStatus | None
    to_status: WorkOrderStatus
    actor_user_id: UUID
    actor_role: str
    reason: str | None
    created_at: datetime


class WorkOrderEventListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkOrderEventData]


WorkOrderPageResponse = SuccessResponse[WorkOrderPageData]
WorkOrderEventListResponse = SuccessResponse[WorkOrderEventListData]
WorkOrderRatingResponse = SuccessResponse[WorkOrderRatingData]


def work_order_data(
    item: WorkOrder, rating: WorkOrderRating | None = None
) -> WorkOrderData:
    return WorkOrderData(
        id=item.id,
        order_no=item.order_no,
        created_by=item.created_by,
        campus_code=item.campus_code,
        dormitory_area=item.dormitory_area,
        building=item.building,
        room=item.room,
        fault_category=item.fault_category,
        description=item.description,
        preferred_start_at=item.preferred_start_at,
        preferred_end_at=item.preferred_end_at,
        status=item.status,
        assigned_to=item.assigned_to,
        assigned_department_id=item.assigned_department_id,
        rejection_reason=item.rejection_reason,
        completion_note=item.completion_note,
        rating=(
            WorkOrderRatingData(
                id=rating.id,
                score=rating.score,
                comment=rating.comment,
                created_at=rating.created_at,
            )
            if rating is not None
            else None
        ),
        version=item.version,
        submitted_at=item.submitted_at,
        accepted_at=item.accepted_at,
        processing_at=item.processing_at,
        completed_at=item.completed_at,
        cancelled_at=item.cancelled_at,
        rejected_at=item.rejected_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def work_order_event_data(item: WorkOrderEvent) -> WorkOrderEventData:
    return WorkOrderEventData(
        id=item.id,
        sequence_no=item.sequence_no,
        event_type=item.event_type,
        from_status=item.from_status,
        to_status=item.to_status,
        actor_user_id=item.actor_user_id,
        actor_role=item.actor_role,
        reason=item.reason,
        created_at=item.created_at,
    )
