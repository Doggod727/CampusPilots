from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.responses import SuccessResponse


class ServiceProgressQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_code: str = Field(min_length=1, max_length=64)
    business_no: str = Field(min_length=6, max_length=64)


class ServiceProgressData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_code: str
    business_no_masked: str
    status: Literal["submitted", "reviewing", "approved", "rejected", "completed"]
    status_text: str
    next_action: str | None
    updated_at: datetime
    source: Literal["mock", "external"]


ServiceProgressResponse = SuccessResponse[ServiceProgressData]
