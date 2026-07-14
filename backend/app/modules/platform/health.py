from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/health", tags=["Health"])


class HealthData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["alive"] = "alive"


@router.get(
    "/live",
    operation_id="getLiveness",
    response_model=SuccessResponse[HealthData],
)
async def get_liveness(request: Request) -> SuccessResponse[HealthData]:
    return SuccessResponse(
        data=HealthData(),
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
