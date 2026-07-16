from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.campus_service.models import WorkOrder
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


def work_order_data(item: WorkOrder) -> WorkOrderData:
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
        rating=None,
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
