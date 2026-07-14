from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import get_settings
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.dashboard import DashboardQueryService, dashboard_service_context
from app.shared.responses import SuccessResponse
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])


class MetricPointData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: date
    value: float


class DashboardSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_users: int
    chat_messages: int
    work_orders: int
    posts: int
    lost_found_items: int
    moderation_pending: int
    llm_tokens: int


class DashboardMetricsData(BaseModel):
    from_: date = Field(alias="from")
    to: date
    granularity: Literal["day", "week"]
    summary: DashboardSummaryData
    series: dict[str, list[MetricPointData]]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


DashboardMetricsResponse = SuccessResponse[DashboardMetricsData]


async def get_service() -> AsyncIterator[DashboardQueryService]:
    async with dashboard_service_context(get_settings()) as service:
        yield service


@router.get("/metrics", operation_id="getDashboardMetrics", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("dashboard:read"))],
    service: Annotated[DashboardQueryService, Depends(get_service)],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    granularity: Literal["day", "week"] = "day",
) -> DashboardMetricsResponse:
    data = await service.get_metrics(from_date=from_date, to_date=to_date, granularity=granularity)
    return SuccessResponse(
        data=DashboardMetricsData.model_validate(data), request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
